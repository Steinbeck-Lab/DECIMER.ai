"""CDK 2.8 for real: the fallback for SMILES RDKit rejects.

Slow: `pytest -m slow tests/test_cdk_integration.py`. Needs a Java 11+ runtime (JAVA_HOME or `java`
on PATH) and the three jars in CHEM_JARS_DIR (OPSIN, centres and cdk-2.8.jar:
`scripts/fetch_jars.py` or `deploy/weights/fetch.sh chem` puts them on disk). Nothing here
downloads anything.

RDKit is the independent oracle throughout: it decides that a SMILES is not readable, and it reads
back the molblock CDK wrote. The expected atom and bond counts are counted by hand from the SMILES.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import math
import subprocess
import sys
import time
import zipfile
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import orthonym.jars
import pytest
import pytest_asyncio
from rdkit import Chem, RDLogger

from decimer_chem.app import CAFFEINE_NAME, CAFFEINE_SMILES, build_app
from decimer_chem.settings import Settings
from decimer_chem.supervisor import ProcessNamer

pytestmark = pytest.mark.slow
module_loop = pytest.mark.asyncio(loop_scope="module")

REPO = Path(__file__).resolve().parents[3]
README = Path(__file__).resolve().parents[1] / "README.md"
RDLogger.DisableLog("rdApp.*")

# Written out, not taken from the code under test: app/Python/cdk-2.8.jar of the earlier Laravel
# version of decimer.ai, which equals the asset of the official release
# https://github.com/cdk/cdk/releases/tag/cdk-2.8.
CDK_SHA256 = "5dff6e365ecc21020e44aa31861b36a9cce34c51ed0b8713b8d25275552239b4"
CDK_BYTES = 47_174_875


def jars_dir() -> Path:
    settings = Settings.from_env()
    return settings.jars_dir or Path(orthonym.jars.jar_dir())


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # The whole production path: build_app() with the real ProcessNamer; the lifespan starts the
    # naming process (JVM, the three jar checks, CDK) and runs both selftests.
    app = build_app(settings=Settings.from_env())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chem", timeout=120) as c:
            yield c


async def cdk(client: httpx.AsyncClient, *smiles: str) -> list[dict[str, Any]]:
    r = await client.post("/cdk", json={"smiles": list(smiles)})
    assert r.status_code == 200, r.text
    results: list[dict[str, Any]] = r.json()["results"]
    assert len(results) == len(smiles)
    return results


def counts_line(molfile: str) -> tuple[int, int]:
    line = molfile.splitlines()[3]
    assert line.rstrip().endswith("V2000")
    return int(line[0:3]), int(line[3:6])


def read_back(molfile: str) -> Chem.Mol:
    """RDKit's reading of CDK's molblock, unsanitised (the structures here break valence rules)."""
    mol = Chem.MolFromMolBlock(molfile, sanitize=False, removeHs=False)
    assert mol is not None
    return mol


def check_layout(mol: Chem.Mol) -> None:
    """The coordinates are a real 2D layout: flat, not all zero, no two atoms on top of each other,
    and every bond about as long as CDK's standard bond length (1.5)."""
    positions = mol.GetConformer().GetPositions()
    assert all(p[2] == 0.0 for p in positions)
    if mol.GetNumAtoms() > 1:
        assert any(abs(p[0]) > 0.1 or abs(p[1]) > 0.1 for p in positions)
        assert min(math.dist(a, b) for a, b in itertools.combinations(positions, 2)) > 0.5
    for bond in mol.GetBonds():
        length = math.dist(positions[bond.GetBeginAtomIdx()], positions[bond.GetEndAtomIdx()])
        assert 1.4 < length < 1.6


# --- the jar -------------------------------------------------------------------------------------


def test_the_cdk_jar_on_disk_is_the_official_release_legacy_uses() -> None:
    jar = jars_dir() / "cdk-2.8.jar"
    assert jar.stat().st_size == CDK_BYTES
    assert hashlib.sha256(jar.read_bytes()).hexdigest() == CDK_SHA256
    assert CDK_SHA256 in README.read_text()
    sums = (REPO / "deploy" / "weights" / "SHA256SUMS").read_text().splitlines()
    assert f"{CDK_SHA256}  chem/cdk-2.8.jar" in sums


def test_the_centres_jar_bundles_its_own_cdk_which_is_why_cdk_gets_a_class_loader_of_its_own() -> (
    None
):
    # On one class path, whichever jar comes first would win: centres' own SmilesParser (another
    # build than 2.8's) would parse for us, or 2.8's classes would replace the ones centres runs on.
    path = "org/openscience/cdk/smiles/SmilesParser.class"
    with zipfile.ZipFile(jars_dir() / "centres-cli-1.2.1.jar") as centres:
        bundled = centres.read(path)
    with zipfile.ZipFile(jars_dir() / "cdk-2.8.jar") as cdk_jar:
        official = cdk_jar.read(path)
    assert bundled != official


@module_loop
async def test_cdk_is_loaded_from_the_checked_jar_and_is_version_2_8(
    client: httpx.AsyncClient,
) -> None:
    namer = client._transport.app.state.namer  # type: ignore[attr-defined]
    assert isinstance(namer, ProcessNamer)
    info = namer.info
    assert info["cdk"] == "2.8"
    assert info["cdk_loaded_from"] == str((jars_dir() / "cdk-2.8.jar").resolve())  # not centres'
    assert info["jars"]["cdk"] == CDK_SHA256  # type: ignore[index]


# --- what CDK does with a SMILES -----------------------------------------------------------------


@module_loop
async def test_caffeine_is_laid_out_and_rdkit_reads_back_the_same_molecule(
    client: httpx.AsyncClient,
) -> None:
    [result] = await cdk(client, CAFFEINE_SMILES)
    assert result["reason"] is None
    molfile = result["molfile"]
    assert counts_line(molfile) == (14, 15)  # C8H10N4O2: 14 heavy atoms, 13 + 2 rings = 15 bonds
    assert molfile.splitlines()[1].startswith("  CDK")  # CDK wrote it
    assert molfile.rstrip().endswith("M  END")
    mol = Chem.MolFromMolBlock(molfile)  # sanitised: this one is a valid molecule
    assert mol is not None
    assert Chem.MolToSmiles(mol) == Chem.MolToSmiles(Chem.MolFromSmiles(CAFFEINE_SMILES))
    check_layout(mol)


@dataclass(frozen=True)
class Accepted:
    smiles: str
    atoms: Counter[str]
    bonds: int
    orders: Counter[float]  # bond orders in the molblock: 1.0 single, 2.0 double
    max_degree: int  # the neighbours of the atom that breaks RDKit's valence rule
    why_rdkit_rejects: str


# SMILES that RDKit's parser rejects and CDK 2.8 reads and lays out (the parser "is much more
# tolerant", in the words of the earlier version). Counted by hand from the SMILES; a valence rule
# is what RDKit refuses.
ACCEPTED = [
    Accepted(
        "CC(C)(C)(C)c1ccccc1",  # tert-butyl with one methyl too many: a carbon with five neighbours
        Counter({"C": 11}),
        11,  # 5 single bonds around the centre, 6 in the ring
        Counter({1.0: 8, 2.0: 3}),  # the ring is kekulised: 3 double bonds
        5,
        "valence of carbon 5 > 4",
    ),
    Accepted(
        "C[N](C)(C)(C)C",  # an ammonium written without its charge: five methyls on a nitrogen
        Counter({"C": 5, "N": 1}),
        5,
        Counter({1.0: 5}),
        5,
        "valence of nitrogen 5 > 3",
    ),
    Accepted(
        "C1=CC=CC=C1=O",  # a carbonyl on a ring carbon that already has its double bond
        Counter({"C": 6, "O": 1}),
        7,  # 6 ring bonds and the C=O
        Counter({1.0: 3, 2.0: 4}),
        3,
        "valence of carbon 5 > 4",
    ),
    Accepted(
        "CS(=O)(=O)(=O)(=O)C",  # a sulfur with four oxygens double-bonded and two methyls
        Counter({"C": 2, "S": 1, "O": 4}),
        6,
        Counter({1.0: 2, 2.0: 4}),
        6,
        "valence of sulfur 10 > 6",
    ),
]


@module_loop
@pytest.mark.parametrize("case", ACCEPTED, ids=[c.smiles for c in ACCEPTED])
async def test_cdk_accepts_and_lays_out_a_smiles_rdkit_rejects(
    client: httpx.AsyncClient, case: Accepted
) -> None:
    assert Chem.MolFromSmiles(case.smiles) is None  # the premise: RDKit refuses it
    [result] = await cdk(client, case.smiles)
    assert result["reason"] is None
    molfile = result["molfile"]
    assert counts_line(molfile) == (sum(case.atoms.values()), case.bonds)
    mol = read_back(molfile)
    assert Counter(a.GetSymbol() for a in mol.GetAtoms()) == case.atoms
    assert mol.GetNumBonds() == case.bonds
    assert Counter(b.GetBondTypeAsDouble() for b in mol.GetBonds()) == case.orders
    assert max(a.GetDegree() for a in mol.GetAtoms()) == case.max_degree  # the odd atom is there
    check_layout(mol)


# SMILES that both RDKit and CDK refuse: the decline of the fallback.
REFUSED = [
    "C1CC",  # an open ring (a control)
    "c",  # an aromatic atom outside a ring
    "c1cccc1",  # an aromatic ring that cannot be kekulised
    "n1cccc1",  # pyrrole without its hydrogen
    "C(C",  # an open branch
    "C1CC1(",  # a branch that never closes
]


@module_loop
@pytest.mark.parametrize("smiles", REFUSED)
async def test_a_smiles_both_parsers_reject_is_declined(
    client: httpx.AsyncClient, smiles: str
) -> None:
    assert Chem.MolFromSmiles(smiles) is None
    assert await cdk(client, smiles) == [{"molfile": None, "reason": "invalid_smiles"}]


@module_loop
async def test_a_batch_comes_back_in_order_with_declines_in_place(
    client: httpx.AsyncClient,
) -> None:
    smiles = ["CCO", "C1CC", ACCEPTED[0].smiles, "c", CAFFEINE_SMILES, "[Na+]"]
    results = await cdk(client, *smiles)
    assert [r["reason"] for r in results] == [None, "invalid_smiles", None, "invalid_smiles"] + [
        None,
        None,
    ]
    assert [counts_line(r["molfile"]) if r["molfile"] else None for r in results] == [
        (3, 2),
        None,
        (11, 11),
        None,
        (14, 15),
        (1, 0),
    ]


@module_loop
async def test_a_salt_and_a_stereocentre_are_written_with_their_charges_and_a_wedge(
    client: httpx.AsyncClient,
) -> None:
    salt, alanine = await cdk(client, "CC(=O)[O-].[Na+]", "C[C@H](N)C(=O)O")
    assert counts_line(salt["molfile"]) == (5, 3)
    assert "M  CHG  1   4  -1" in salt["molfile"] and "M  CHG  1   5   1" in salt["molfile"]
    lines = alanine["molfile"].splitlines()
    atoms, bonds = counts_line(alanine["molfile"])
    bond_lines = lines[4 + atoms : 4 + atoms + bonds]
    assert len(bond_lines) == bonds == 5
    assert [int(line[9:12]) for line in bond_lines].count(1) == 1  # one wedge: the stereo flag


# --- the limits ----------------------------------------------------------------------------------

# The cap is 300 atoms and 300 bonds: DECIMER decodes at most 302 tokens (assets/max_length.pkl), so
# it cannot write more (README, "CDK fallback"). 49 benzenes and six more carbons are exactly 300
# atoms; the layout of separate fragments is quick, so these are cheap to lay out.
FRAGMENTS_300 = "c1ccccc1." * 49 + "CCCCCC"
FRAGMENTS_301 = "c1ccccc1." * 49 + "CCCCCCC"
# The structure that cost a batch of 20 a minute and four replaced JVMs: 855 atoms, 19 s each to lay
# out. (Also a slow one of 300 atoms, which DECIMER cannot write, for the timeout.)
SLOW_855 = "C(C)(C)" * 285
SLOW_300 = "C(C)(C)" * 100


@module_loop
async def test_300_atoms_are_laid_out_and_301_are_declined_before_the_layout(
    client: httpx.AsyncClient,
) -> None:
    fits, over = await cdk(client, FRAGMENTS_300, FRAGMENTS_301)
    assert counts_line(fits["molfile"]) == (300, 299)
    assert read_back(fits["molfile"]).GetNumAtoms() == 300
    assert over == {"molfile": None, "reason": "too_large"}


@module_loop
@pytest.mark.parametrize(
    "smiles",
    [SLOW_855, "C" * 999, "C" * 2000, "c1ccccc1." * 166 + "CCC"],
    ids=["855-atoms-19-s", "chain-999-8-s", "chain-2000-44-s", "999-atoms-in-fragments"],
)
async def test_a_too_large_smiles_is_declined_fast_and_never_reaches_the_layout(
    client: httpx.AsyncClient, smiles: str
) -> None:
    started = time.monotonic()
    assert await cdk(client, smiles) == [{"molfile": None, "reason": "too_large"}]
    assert (
        time.monotonic() - started < 3
    )  # parsed, counted and declined: laying out takes 8 to 44 s


@module_loop
async def test_twenty_slow_structures_neither_hold_the_process_nor_make_a_name_wait(
    client: httpx.AsyncClient,
) -> None:
    namer = client._transport.app.state.namer  # type: ignore[attr-defined]
    pid, started = namer.pid, time.monotonic()
    laid_out, named = await asyncio.gather(
        client.post("/cdk", json={"smiles": [SLOW_855] * 20}),
        client.post("/name", json={"smiles": "CCO"}),
    )
    assert [r["reason"] for r in laid_out.json()["results"]] == ["too_large"] * 20
    assert named.json()["name"] == "ethanol"
    assert time.monotonic() - started < 10  # it used to be 62 s, and four JVM replacements
    assert namer.pid == pid and namer.hard_kills == 0


@module_loop
async def test_a_smiles_over_2000_characters_is_a_422_and_cdk_is_not_asked(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/cdk", json={"smiles": ["C" * 2001]})
    assert r.status_code == 422


async def test_a_conversion_that_takes_too_long_is_a_timeout_and_the_process_is_replaced() -> None:
    # 100 x `C(C)(C)` is 300 atoms (allowed) and takes the layout about 1.5 s; the limit is 0.3 s.
    namer = ProcessNamer(replace(Settings.from_env(), cdk_timeout_s=0.3))
    await namer.start()
    spawned: list[int] = []
    real_spawn = namer._spawn

    async def spy() -> object:
        spawned.append(1)
        return await real_spawn()

    namer._spawn = spy  # type: ignore[method-assign]
    try:
        first = namer.pid
        assert first is not None
        started = time.monotonic()
        results = await namer.cdk_many([CAFFEINE_SMILES, SLOW_300, SLOW_300, CAFFEINE_SMILES])
        elapsed = time.monotonic() - started
        # The first timeout ends the batch: the rest is a timeout, never sent, so one replacement.
        assert [r.reason for r in results] == [None, "timeout", "timeout", "timeout"]
        assert all(r.molfile is None for r in results[1:])
        assert elapsed < 20  # one JVM start for the successor, not one per structure
        assert namer.hard_kills == 0  # the soft limit answered; nobody had to be killed for silence
        deadline = time.monotonic() + 30
        while namer.pid in (None, first) and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert namer.pid not in (None, first)  # a Java thread cannot be stopped: it was replaced
        assert spawned == [1]  # once
        deadline = time.monotonic() + 15
        while Path(f"/proc/{first}").exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert not Path(f"/proc/{first}").exists()  # and the one with the runaway thread is gone
        [again] = await namer.cdk_many(["CCO"])  # the replacement works
        assert again.molfile is not None
    finally:
        await namer.stop()


# --- the two start-up checks of the class loader -------------------------------------------

SHADOW_SCRIPT = """
import json
from pathlib import Path

import jpype

import decimer_chem.cdk as cdk_module
from decimer_chem.cdk import CdkLayout
from decimer_chem.engine import OrthonymEngine
from decimer_chem.settings import Settings

engine = OrthonymEngine(Settings.from_env())
info = engine.start()  # starts the JVM as the service does: OPSIN and centres on its class path
jar = Path(str(info["cdk_loaded_from"]))
system = jpype.JClass("java.lang.ClassLoader").getSystemClassLoader()


class SharedLoader(CdkLayout):
    # CDK's loader as it must never be: its parent is the system loader, where centres' CDK is seen
    @staticmethod
    def _parent_loader():
        return system


def outcome(layout, expected_version="2.8"):
    cdk_module.CDK_VERSION = expected_version
    try:
        layout.start()
        return "started"
    except RuntimeError as exc:
        return str(exc)
    finally:
        cdk_module.CDK_VERSION = "2.8"


print(json.dumps({
    "centres_cdk_version": str(jpype.JClass("org.openscience.cdk.CDK", loader=system).getVersion()),
    "isolated": outcome(CdkLayout(jar)),
    "shared": outcome(SharedLoader(jar)),
    # the version seen there (2.9) is the one expected: only the check of the location fires
    "shared_but_version_agrees": outcome(SharedLoader(jar), "2.9"),
    # the right loader, but a version that is not the one on disk: only the version check fires
    "isolated_but_version_differs": outcome(CdkLayout(jar), "2.7"),
}))
"""


def test_start_refuses_a_shadowed_cdk_and_a_cdk_of_another_version() -> None:
    out = subprocess.run(
        [sys.executable, "-c", SHADOW_SCRIPT],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    report = json.loads(out.stdout.splitlines()[-1])
    assert report["centres_cdk_version"] == "2.9"  # the premise: the other CDK is not 2.8
    assert report["isolated"] == "started"  # the control: the real loader passes both checks
    # With the system loader as parent, centres' SmilesParser wins and the start is refused ...
    assert "was loaded from" in report["shared"] and "centres-cli-1.2.1.jar" in report["shared"]
    # ... and each check refuses on its own, so deleting either one fails this test:
    assert "was loaded from" in report["shared_but_version_agrees"]  # the location check
    assert "is CDK 2.8, but the service is built for 2.7" in report["isolated_but_version_differs"]


# --- the hash check, and the jar -----------------------------------------------------------------


def jars_copy(directory: Path, *, cdk: bytes | None) -> Path:
    """A jar directory with the real OPSIN and centres jars (linked) and the given cdk-2.8.jar."""
    for name in ("opsin-cli-2.9.0-jar-with-dependencies.jar", "centres-cli-1.2.1.jar"):
        (directory / name).symlink_to(jars_dir() / name)
    if cdk is not None:
        (directory / "cdk-2.8.jar").write_bytes(cdk)
    return directory


async def refused_at_start(directory: Path) -> str:
    namer = ProcessNamer(replace(Settings.from_env(), jars_dir=directory, startup_timeout_s=60))
    with pytest.raises(RuntimeError) as info:
        await namer.start()
    return str(info.value)


async def test_a_cdk_jar_that_is_not_the_official_one_is_refused_before_the_jvm_starts(
    tmp_path: Path,
) -> None:
    message = await refused_at_start(jars_copy(tmp_path, cdk=b"not the jar"))
    assert "SHA-256" in message and CDK_SHA256 in message


async def test_a_cdk_jar_that_differs_from_the_official_one_by_one_byte_is_refused(
    tmp_path: Path,
) -> None:
    real = bytearray((jars_dir() / "cdk-2.8.jar").read_bytes())
    real[len(real) // 2] ^= 0x01  # somewhere in the middle of the archive
    message = await refused_at_start(jars_copy(tmp_path, cdk=bytes(real)))
    assert "SHA-256" in message and CDK_SHA256 in message


async def test_a_missing_cdk_jar_is_refused_even_though_the_other_two_are_there(
    tmp_path: Path,
) -> None:
    message = await refused_at_start(jars_copy(tmp_path, cdk=None))
    assert str(tmp_path / "cdk-2.8.jar") in message and "never downloads" in message


# --- offline, and the web process ----------------------------------------------------------------

OFFLINE_SCRIPT = """
import json, os, socket, urllib.request
for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ[key] = "http://127.0.0.1:9"  # a dead address
os.environ.pop("CHEM_ALLOW_JAR_DOWNLOAD", None)

def deny(*args, **kwargs):
    raise AssertionError("the network was used")

socket.socket.connect = deny
socket.create_connection = deny
urllib.request.urlopen = deny
import orthonym.jars
orthonym.jars._download = deny

from decimer_chem.engine import OrthonymEngine
from decimer_chem.settings import Settings

engine = OrthonymEngine(Settings.from_env())
info = engine.start()
result = engine.cdk("CC(C)(C)(C)c1ccccc1", 20)
print(json.dumps({"cdk": info["cdk"], "counts": result.molfile.splitlines()[3][:6]}))
"""


def test_the_engine_starts_and_lays_out_with_the_network_unavailable() -> None:
    out = subprocess.run(
        [sys.executable, "-I", "-c", OFFLINE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.splitlines()[-1]) == {"cdk": "2.8", "counts": " 11 11"}


WEB_PROCESS_SCRIPT = """
import asyncio, json, sys
from decimer_chem.settings import Settings
from decimer_chem.supervisor import ProcessNamer

async def main():
    namer = ProcessNamer(Settings.from_env())
    await namer.start()
    results = await namer.cdk_many(["CC(C)(C)(C)c1ccccc1", "c", "CCO"])
    out = {
        "reasons": [r.reason for r in results],
        "plain": all(type(f) is str for r in results for f in (r.molfile, r.reason) if f),
        "modules": sorted(m for m in ("jpype", "orthonym", "rdkit") if m in sys.modules),
    }
    await namer.stop()
    print(json.dumps(out))

if __name__ == "__main__":
    asyncio.run(main())
"""


def test_the_web_process_never_imports_the_jvm_bridge_orthonym_or_rdkit_for_cdk() -> None:
    out = subprocess.run(
        [sys.executable, "-c", WEB_PROCESS_SCRIPT],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    report = json.loads(out.stdout.splitlines()[-1])
    assert report == {"reasons": [None, "invalid_smiles", None], "plain": True, "modules": []}


LEAK_SCRIPT = """
import gc, json
from decimer_chem.engine import OrthonymEngine
from decimer_chem.settings import Settings

def status(field):
    for line in open("/proc/self/status"):
        if line.startswith(field + ":"):
            return int(line.split()[1])

engine = OrthonymEngine(Settings.from_env())
engine.start()
mixed = [
    "CC(C)(C)(C)c1ccccc1",
    "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "C1CC",
    "CC(=O)[O-].[Na+]",
    "C[C@H](N)C(=O)O",
]
for i in range(4000):  # the JVM's heap settles during the first few thousand structures
    engine.cdk(mixed[i % 5], 10)
gc.collect()
before = (len(gc.get_objects()), status("VmRSS"), status("Threads"))
for i in range(6000):  # half of them declines
    engine.cdk("C1CC" if i % 2 else mixed[i % 5], 10)
gc.collect()
after = (len(gc.get_objects()), status("VmRSS"), status("Threads"))
print(json.dumps({"objects": after[0] - before[0], "rss_mb": (after[1] - before[1]) // 1024,
                  "threads": after[2] - before[2]}))
"""


def test_many_structures_neither_declines_nor_layouts_grow_the_process() -> None:
    # JPype keeps what it makes for a Python thread that calls Java (about 8 KB) and for a Java
    # exception turned into a Python one (about 1 KB and three objects): a thread per call and
    # exceptions for declines grew the naming process by 8 MB per 1000 structures, for ever.
    out = subprocess.run(
        [sys.executable, "-c", LEAK_SCRIPT],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    report = json.loads(out.stdout.splitlines()[-1])
    # What leaked was Python objects (3000 declines left 9000, 6000 calls on a thread each 21,000),
    # and those count exactly. The resident size is reported but not asserted: it also steps up by
    # tens of MB whenever the JVM happens to commit more heap, which depends on the box's load.
    assert report["objects"] < 300, report
    assert report["threads"] == 0, report


# --- one JVM for both ----------------------------------------------------------------------------


@module_loop
async def test_names_and_layouts_share_one_process_and_neither_disturbs_the_other(
    client: httpx.AsyncClient,
) -> None:
    namer = client._transport.app.state.namer  # type: ignore[attr-defined]
    pid = namer.pid
    named, laid_out, named_again = await asyncio.gather(
        client.post("/name", json={"smiles": CAFFEINE_SMILES}),
        client.post("/cdk", json={"smiles": [CAFFEINE_SMILES, ACCEPTED[1].smiles]}),
        client.post("/names", json={"smiles": ["CCO"]}),
    )
    assert named.json()["name"] == CAFFEINE_NAME  # OPSIN and centres still work beside CDK
    assert named_again.json()["results"][0]["name"] == "ethanol"
    assert [r["reason"] for r in laid_out.json()["results"]] == [None, None]
    assert namer.pid == pid and namer.hard_kills == 0
    assert (await client.get("/readyz")).json() == {"status": "ready", "service": "chem"}
