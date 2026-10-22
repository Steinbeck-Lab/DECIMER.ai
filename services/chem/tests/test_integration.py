"""The real thing: Orthonym in a JVM, behind the real service. Slow: `pytest -m slow -s`.

Needs a Java 11+ runtime (JAVA_HOME or `java` on PATH) and the two jars in CHEM_JARS_DIR (default:
Orthonym's cache, filled by `scripts/fetch_jars.py`). Nothing here downloads anything.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
import orthonym.jars
import pytest
import pytest_asyncio
from rdkit import Chem, RDLogger

from decimer_chem.app import CAFFEINE_NAME, CAFFEINE_SMILES, build_app
from decimer_chem.results import ROUND_TRIPPED
from decimer_chem.settings import Settings
from decimer_chem.supervisor import ProcessNamer

pytestmark = pytest.mark.slow
module_loop = pytest.mark.asyncio(loop_scope="module")

REPO = Path(__file__).resolve().parents[3]
README = Path(__file__).resolve().parents[1] / "README.md"
# Written out, not taken from the code under test.
VERIFIED_VALUES = {
    "opsin",
    "opsin_constitution",
    "identity",
    "opsin_split",
    "opsin_twin",
    "opsin_acids",
}
TIERS = {"pin_verified", "pin_unverified", "systematic_verified"}

# The jars Orthonym pins (also in README.md: the image build bakes exactly these).
OPSIN_SHA256 = "c2e29326c281f87b59a05d934d8589adac6e9d17b95b984931b3e739111b360f"
CENTRES_SHA256 = "6c5d5cafa9393ed7b667a2340222348cd15a9bbdd0d1b92518229f26cc5e87fb"

RDLogger.DisableLog("rdApp.*")


def jars_dir() -> Path:
    settings = Settings.from_env()
    return settings.jars_dir or Path(orthonym.jars.jar_dir())


def java() -> str:
    home = os.environ.get("JAVA_HOME")
    found = str(Path(home) / "bin" / "java") if home else shutil.which("java")
    if not found or not Path(found).is_file():
        pytest.fail("no Java runtime: set JAVA_HOME or put `java` on PATH")
    return found


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # The whole production path: build_app() with the real ProcessNamer; the lifespan starts the
    # naming process (JVM, jar check) and runs the caffeine selftest.
    app = build_app(settings=Settings.from_env())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chem", timeout=120) as c:
            yield c


# --- exact names from Orthonym's README -----------------------------------------------------------


@module_loop
@pytest.mark.parametrize(
    ("smiles", "expected"),
    [
        (CAFFEINE_SMILES, CAFFEINE_NAME),
        ("CCO", "ethanol"),
        ("CC(=O)Oc1ccccc1C(=O)O", "2-(acetyloxy)benzoic acid"),
    ],
    ids=["caffeine", "ethanol", "aspirin"],
)
async def test_the_names_in_orthonyms_readme(
    client: httpx.AsyncClient, smiles: str, expected: str
) -> None:
    r = await client.post("/name", json={"smiles": smiles})
    assert r.status_code == 200
    assert r.json() == {
        "name": expected,
        "tier": "pin_verified",
        "verified": "opsin",
        "reason": None,
    }


@module_loop
async def test_readyz_names_caffeine_for_real(client: httpx.AsyncClient) -> None:
    assert (await client.get("/readyz")).json() == {"status": "ready", "service": "chem"}


# --- the 12 compounds of Figure 2 of the golden paper ---------------------------------------------


@dataclass(frozen=True)
class Compound:
    number: int
    smiles: str
    label_inchikey: str


def figure_2() -> list[Compound]:
    with (REPO / "golden" / "labels.csv").open(newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["number"].isdigit()]
    compounds = [Compound(int(r["number"]), r["smiles"], r["inchikey"]) for r in rows]
    assert [c.number for c in compounds] == list(range(1, 13))
    return compounds


def opsin_inchikey(name: str) -> str | None:
    """Read `name` back with the OPSIN command line, in a separate JVM and process from the
    service's own, and return the InChIKey of the structure it gives (None if OPSIN fails)."""
    jar = jars_dir() / "opsin-cli-2.9.0-jar-with-dependencies.jar"
    out = subprocess.run(
        [java(), "-jar", str(jar), "-osmi"],
        input=name + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    smiles = out.stdout.strip()
    mol = Chem.MolFromSmiles(smiles) if smiles else None
    return Chem.MolToInchiKey(mol) if mol is not None else None


def inchikey(smiles: str) -> str:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, smiles
    return Chem.MolToInchiKey(mol)


# Orthonym's `valid` tier names all 12 (owner decision D14). The six that the default tier declines
# (1, 2, 4, 5, 6, 7) come back as OPSIN-checked systematic names that are not preferred names, and
# say so in their tier. Both sets are Orthonym 1.0.8's behaviour at the pinned commit: a bump that
# changes them fails here so that the change is looked at.
NAMED = set(range(1, 13))
SYSTEMATIC = {1, 2, 4, 5, 6, 7}


@module_loop
async def test_figure_2_each_compound_is_named_and_round_trips_or_is_explicitly_declined(
    client: httpx.AsyncClient,
) -> None:
    compounds = figure_2()
    r = await client.post("/names", json={"smiles": [c.smiles for c in compounds]})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 12

    table = [
        "",
        "| # | SMILES | name, or why not | tier | verified | OPSIN read-back |",
        "|---|---|---|---|---|---|",
    ]
    for compound, result in zip(compounds, results, strict=True):
        assert set(result) == {"name", "tier", "verified", "reason"}
        if result["name"] is not None:
            assert result["tier"] in TIERS
            assert result["verified"] in VERIFIED_VALUES
            assert result["verified"] == "opsin"  # all 12 are fully read back at this tier
            assert result["reason"] is None
            # An oracle that does not use the service's own check: OPSIN, run separately by the
            # test, must read the name back to the structure that was sent.
            expected = inchikey(compound.smiles)
            got = opsin_inchikey(result["name"])
            assert got is not None, f"OPSIN cannot read {result['name']!r}"
            if result["verified"] == "opsin":
                assert got == expected, (compound.number, result["name"])
            else:
                assert got.split("-")[0] == expected.split("-")[0], (
                    compound.number,
                    result["name"],
                )
            readback = "same InChIKey" if got == expected else "same skeleton block"
            shown = result["name"]
        else:
            assert result["tier"] == "abstain"
            assert result["verified"] is None
            assert result["reason"]
            readback = "-"
            shown = f"(no verified name: {result['reason']})"
        expected_tier = "systematic_verified" if compound.number in SYSTEMATIC else "pin_verified"
        assert result["tier"] == expected_tier, (compound.number, result["tier"])
        table.append(
            f"| {compound.number} | `{compound.smiles}` | {shown} | {result['tier']} | "
            f"{result['verified']} | {readback} |"
        )
    print("\n".join(table))

    named = {c.number for c, x in zip(compounds, results, strict=True) if x["name"]}
    assert named == NAMED


def test_the_golden_labels_are_the_structures_they_say_they_are() -> None:
    for compound in figure_2():
        assert inchikey(compound.smiles) == compound.label_inchikey, compound.number


# Structures that only a tier wider than `valid` names (Orthonym 1.0.8): `best-effort` gives the
# diol "cis-cyclohexane-1,4-diol", `benzene—iron (1/1)`, and a von Baeyer name for the cyclodextrin.
# `valid` declines all three, and so must the service: a wider tier switched on anywhere turns
# this red.
WIDER_ONLY = {
    "trans-cyclohexane-1,4-diol": "O[C@H]1CC[C@@H](O)CC1",
    "benzene.Fe": "C1=CC=CC=C1.[Fe]",
    "cyclodextrin": (
        "OC[C@H]1O[C@@H]2O[C@H]3[C@H](O)[C@@H](O)[C@H](O[C@H]4[C@H](O)[C@@H](O)[C@H](O[C@H]5"
        "[C@H](O)[C@@H](O)[C@H](O[C@H]6[C@H](O)[C@@H](O)[C@H](O[C@H]7[C@H](O)[C@@H](O)[C@H](O"
        "[C@H]8[C@H](O)[C@@H](O)[C@H](O[C@H]1[C@H](O)[C@H]2O)O8)O7)O6)O5)O4)O3"
    ),
}


@module_loop
@pytest.mark.parametrize("smiles", WIDER_ONLY.values(), ids=WIDER_ONLY.keys())
async def test_no_tier_wider_than_valid_is_used(client: httpx.AsyncClient, smiles: str) -> None:
    body = (await client.post("/name", json={"smiles": smiles})).json()
    assert body["name"] is None
    assert body["tier"] == "abstain"
    assert body["reason"]


# The reviewer's cases. At Orthonym's *default* tier all three are shipped as `pin_unverified` with
# `verified: "unverified"` (no round trip); at the `valid` tier two are declined and the third gets
# a different, OPSIN-checked name.
CARVE_OUTS = {
    "scyllo-inositol": ("O[C@H]1[C@H](O)[C@@H](O)[C@H](O)[C@@H](O)[C@@H]1O", "scyllo-inositol"),
    "1,4(1,4)-dibenzenacyclohexaphane": (
        "c1cc2ccc1CCc1ccc(cc1)CC2",
        "1,4(1,4)-dibenzenacyclohexaphane",
    ),
    "(eta6-benzene)iron": ("C1=CC=CC=C1.[Fe]", "(η⁶-benzene)iron"),
}


@module_loop
@pytest.mark.parametrize(("smiles", "carve_out_name"), CARVE_OUTS.values(), ids=CARVE_OUTS.keys())
async def test_the_carve_out_names_are_not_served_by_the_service(
    client: httpx.AsyncClient, smiles: str, carve_out_name: str
) -> None:
    body = (await client.post("/name", json={"smiles": smiles})).json()
    assert body["name"] != carve_out_name
    if body["name"] is not None:  # named by the general engine instead: checked and labelled
        assert body["verified"] in ROUND_TRIPPED
        assert body["tier"] in TIERS
    else:
        assert body["reason"]


async def test_the_policy_withholds_the_real_carve_out_rows_of_the_default_tier() -> None:
    # The same three structures through an engine built for Orthonym's default tier (a test-only
    # class): Orthonym really produces the names, and the policy withholds every one.
    namer = ProcessNamer(Settings.from_env(), factory="real_engines:make_default_tier_engine")
    await namer.start()
    try:
        results = await namer.name_many([smiles for smiles, _ in CARVE_OUTS.values()])
    finally:
        await namer.stop()
    for result in results:
        assert result.name is None
        assert result.tier == "pin_unverified"  # a name did come out, in this tier ...
        assert result.reason == "not_round_tripped"  # ... and was kept back


@module_loop
async def test_a_pin_unverified_name_with_a_full_round_trip_is_served(
    client: httpx.AsyncClient,
) -> None:
    # sphingosine: at the valid tier a PIN-form name that OPSIN reads back, not certified as the PIN
    smiles = "CCCCCCCCCCCCC/C=C/[C@@H](O)[C@@H](N)CO"
    body = (await client.post("/name", json={"smiles": smiles})).json()
    assert body == {
        "name": "(2S,3R,4E)-2-aminooctadec-4-ene-1,3-diol",
        "tier": "pin_unverified",
        "verified": "opsin",
        "reason": None,
    }


# --- errors and limits with the real engine -------------------------------------------------------


@module_loop
async def test_an_unreadable_smiles_is_a_422_and_the_service_carries_on(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.post("/name", json={"smiles": "C1CC"})).status_code == 422  # open ring
    assert (await client.post("/name", json={"smiles": "c1ccccc"})).status_code == 422
    r = await client.post("/names", json={"smiles": ["CCO", "C1CC", "CC(=O)O"]})
    assert r.status_code == 200  # in a batch it is the one entry that fails
    results = r.json()["results"]
    assert [x["reason"] for x in results] == [None, "invalid_smiles", None]
    assert [x["name"] for x in results] == ["ethanol", None, "acetic acid"]
    assert (await client.post("/name", json={"smiles": "CCO"})).json()["name"] == "ethanol"


@module_loop
async def test_structures_orthonym_declines_for_other_reasons_are_declines_not_errors(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/names", json={"smiles": ["[Na+]", "[H]", "O"]})
    assert r.status_code == 200
    declined = r.json()["results"]
    assert declined[0]["name"] is None
    assert declined[0]["reason"] == "UNSUPPORTED_ELEMENT"
    assert declined[2]["name"] == "water"  # and a plain one in the same batch is named


@module_loop
async def test_the_jvm_is_started_once_and_one_process_serves_every_request(
    client: httpx.AsyncClient,
) -> None:
    namer = client._transport.app.state.namer  # type: ignore[attr-defined]
    assert isinstance(namer, ProcessNamer)
    pid = namer.pid
    assert pid is not None
    for _ in range(5):
        await client.post("/names", json={"smiles": ["CCO", "CCN", "CC(=O)O"] * 6})
    assert namer.pid == pid
    assert namer.hard_kills == 0
    assert str(namer.info["java"]).startswith("21")


# --- a real timeout -------------------------------------------------------------------------------

# A PAMAM-style dendrimer: Orthonym takes about 30 s to decline it.
DENDRIMER = (
    "C(CN(CCC(=O)NCCN(CCC(=O)OC)CCC(=O)OC)CCC(=O)NCCN(CCC(=O)OC)CCC(=O)OC)N(CCC(=O)NCCN"
    "(CCC(=O)OC)CCC(=O)OC)CCC(=O)NCCN(CCC(=O)OC)CCC(=O)OC"
)


async def test_a_molecule_that_takes_too_long_is_declined_as_timeout_and_the_engine_survives() -> (
    None
):
    namer = ProcessNamer(replace(Settings.from_env(), name_timeout_s=1.0))
    await namer.start()
    try:
        pid = namer.pid
        started = time.monotonic()
        results = await namer.name_many(["CCO", DENDRIMER, CAFFEINE_SMILES])
        elapsed = time.monotonic() - started
        assert [r.name for r in results] == ["ethanol", None, CAFFEINE_NAME]
        assert results[1].reason == "timeout"
        assert elapsed < 10  # not the ~30 s Orthonym would need
        assert namer.hard_kills == 0  # Orthonym's own limit did it; no process was killed
        assert namer.pid == pid  # same JVM, no restart
    finally:
        await namer.stop()


# --- offline start, and the service as a program --------------------------------------------------

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
print(json.dumps({"name": engine.name("CCO", 20).name, "java": info["java"]}))
"""


def test_the_engine_starts_and_names_with_the_network_unavailable() -> None:
    out = subprocess.run(
        [sys.executable, "-I", "-c", OFFLINE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.splitlines()[-1])["name"] == "ethanol"


WEB_PROCESS_SCRIPT = """
import asyncio, json, sys
from decimer_chem.settings import Settings
from decimer_chem.supervisor import ProcessNamer

def rss_mb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) // 1024

async def main():
    namer = ProcessNamer(Settings.from_env())
    await namer.start()
    results = await namer.name_many(
        ["C[Si](C)(C)O[Si](C)(C)C", "C[Si](C)(C)Cl", "CC(=O)Oc1ccccc1C(=O)O"]
    )
    out = {
        "names": [r.name for r in results],
        "plain": all(type(f) is str for r in results for f in (r.name, r.tier, r.verified)),
        "orthonym_imported": "orthonym" in sys.modules,
        "rdkit_imported": "rdkit" in sys.modules,
        "rss_mb": rss_mb(),
    }
    await namer.stop()
    print(json.dumps(out))

if __name__ == "__main__":
    asyncio.run(main())
"""


def test_the_web_process_never_imports_orthonym_or_rdkit_however_the_names_are_made() -> None:
    # Names such as the silyl ethers can be str subclasses inside Orthonym (PrefixName carries a
    # derivation object); the parent must see plain strings only. A web process that imported
    # Orthonym and RDKit grew from 42 MB to 104 MB.
    out = subprocess.run(
        [sys.executable, "-c", WEB_PROCESS_SCRIPT],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    report = json.loads(out.stdout.splitlines()[-1])
    assert all(report["names"])  # all three structures were named
    assert report["plain"] is True
    assert report["orthonym_imported"] is False
    assert report["rdkit_imported"] is False
    assert report["rss_mb"] < 80


NO_PICKLE_SCRIPT = """
import json, pickle

class PickleUsed(BaseException):  # not an Exception: Orthonym's `except Exception` cannot hide it
    pass

def deny(*args, **kwargs):
    raise PickleUsed("pickle.loads or pickle.load was called")

pickle.loads = deny
pickle.load = deny

from decimer_chem.engine import OrthonymEngine
from decimer_chem.settings import Settings

engine = OrthonymEngine(Settings.from_env())
engine.start()
# The second one is named by the general engine, which is what consults the lexicon (and with the
# cache on, would read or write the pickle): ethanol and aspirin alone never touch it.
smiles_list = ("CCO", "OC[C@H](O)C(c1c[nH]c2ccccc12)c1c[nH]c2ccccc12")
names = [engine.name(smiles, 30).name for smiles in smiles_list]
print(json.dumps(names))
"""


@pytest.mark.parametrize("where", ["ORTHONYM_CACHE_DIR", "XDG_CACHE_HOME", "HOME"])
def test_the_service_neither_reads_nor_writes_a_cache_pickle(tmp_path: Path, where: str) -> None:
    # Orthonym's lexicon cache is a pickle written at run time and loaded with pickle.loads. The
    # service switches it off, so a file planted where Orthonym looks must be left alone and
    # nothing may be written there.
    from orthonym.validation.name_morphemes import lexicon_cache_key

    home = tmp_path / "home"
    base = {
        "ORTHONYM_CACHE_DIR": tmp_path / "cache",
        "XDG_CACHE_HOME": tmp_path / "xdg" / "orthonym",
        "HOME": home / ".cache" / "orthonym",
    }[where]
    base.mkdir(parents=True)
    decoy = base / f"lexicon-{lexicon_cache_key()}.pkl"
    decoy.write_bytes(b"planted: not for loading")
    env = {k: v for k, v in os.environ.items() if k not in ("XDG_CACHE_HOME", "ORTHONYM_CACHE_DIR")}
    env["HOME"] = str(home)
    env["CHEM_JARS_DIR"] = str(jars_dir())  # the real jars, wherever the cache would go
    if where == "ORTHONYM_CACHE_DIR":
        env["ORTHONYM_CACHE_DIR"] = str(base)
    if where == "XDG_CACHE_HOME":
        env["XDG_CACHE_HOME"] = str(base.parent)
    out = subprocess.run(
        [sys.executable, "-I", "-c", NO_PICKLE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=env,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.splitlines()[-1]) == [
        "ethanol",
        "(2R)-3,3-bis(1H-indol-3-yl)propane-1,2-diol",
    ]
    written = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert written == [decoy]  # nothing new anywhere
    assert decoy.read_bytes() == b"planted: not for loading"  # and the planted file was not touched


def test_the_jars_on_disk_are_the_ones_the_readme_records() -> None:
    readme = README.read_text()
    for filename, digest in (
        ("opsin-cli-2.9.0-jar-with-dependencies.jar", OPSIN_SHA256),
        ("centres-cli-1.2.1.jar", CENTRES_SHA256),
    ):
        on_disk = hashlib.sha256((jars_dir() / filename).read_bytes()).hexdigest()
        assert on_disk == digest
        assert digest in readme


def test_python_dash_m_serves_json_logs_without_structure_names_and_stops_cleanly() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "decimer_chem"],
        env={**os.environ, "PORT": str(port)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 90
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=2) as ready:  # noqa: S310
                    if ready.status == 200:
                        break
            except OSError:
                pass
            assert process.poll() is None, "the service exited during startup"
            assert time.monotonic() < deadline, "the service did not become ready"
            time.sleep(0.25)
        request = urllib.request.Request(  # noqa: S310
            f"http://127.0.0.1:{port}/name",
            data=json.dumps({"smiles": "CC(=O)Oc1ccccc1C(=O)O"}).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310
            assert json.load(answer)["name"] == "2-(acetyloxy)benzoic acid"
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            output, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            pytest.fail("the service did not stop on SIGTERM")
    # uvicorn finishes its graceful shutdown and then re-raises the signal, so the exit status of a
    # clean stop is "killed by SIGTERM" (-15), not 0.
    assert process.returncode in (0, -signal.SIGTERM)
    lines = [line for line in output.splitlines() if line.strip()]
    assert lines
    records = [json.loads(line) for line in lines]  # every line is JSON, including the child's
    assert any(r["message"] == "named" for r in records)
    assert any(r["message"] == "Application shutdown complete." for r in records)
    assert "acetyloxy" not in output  # Orthonym's INFO lines carry the name it chose
    ready = next(r for r in records if r["message"] == "naming process ready")
    assert not Path(f"/proc/{ready['pid']}").exists()  # the naming process went with the service
