"""GET /api/engines and the list behind it."""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from decimer_api.engines import MODEL_ENGINES, engines
from tests.results import FakeNamer

REPO = Path(__file__).resolve().parents[2]


async def test_the_endpoint_lists_every_engine_with_its_version(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/engines")
    assert response.status_code == 200
    body = response.json()
    assert [e["id"] for e in body["engines"]] == [
        "decimer",
        "decimer-segmentation",
        "decimer-image-classifier",
        "orthonym",
        "opsin",
        "rdkit",
    ]
    by_id = {e["id"]: e for e in body["engines"]}
    assert by_id["decimer"] == {
        "id": "decimer",
        "name": "DECIMER Image Transformer",
        "role": "ocsr",
        "version": "2.8.1",
        "revision": None,
    }
    assert by_id["orthonym"]["revision"] == "110ed8153b365973ad90c518a3ea96fbebb13c8b"
    assert by_id["decimer-segmentation"]["role"] == "segmentation"


async def test_rdkit_is_the_version_this_process_runs(client: httpx.AsyncClient) -> None:
    engines_ = (await client.get("/api/engines")).json()["engines"]
    rdkit = next(e for e in engines_ if e["id"] == "rdkit")
    assert rdkit["version"] == metadata.version("rdkit")


async def test_the_endpoint_calls_no_service(
    app: FastAPI, client: httpx.AsyncClient, namer: FakeNamer
) -> None:
    """Versions are in the process: no model service is asked, so it is cheap on the hot path."""
    for _ in range(3):
        assert (await client.get("/api/engines")).status_code == 200
    assert namer.calls == []


async def test_the_answer_may_be_cached_for_a_few_minutes(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/engines")
    assert response.headers["cache-control"] == "public, max-age=300"


def test_the_list_is_built_once() -> None:
    assert engines() is engines()


# --- the pins the list mirrors --------------------------------------------------------------------

# The pins live in the monorepo (services/*/uv.lock, deploy/): a checkout of only `api/` has nothing
# to compare with. Inside the monorepo these tests FAIL when a file is missing or moved, they do not
# skip: a skip that fires whenever a path changes would hide exactly the drift they exist to catch.
IN_MONOREPO = (REPO / "api" / "pyproject.toml").is_file() and (REPO / "deploy").is_dir()
in_the_monorepo = pytest.mark.skipif(
    not IN_MONOREPO, reason="the api is not run from inside the monorepo: no pins to compare with"
)


def _locked(service: str, package: str) -> tuple[str, str | None]:
    """The version and git commit of `package` in `services/<service>/uv.lock`."""
    lock = tomllib.loads((REPO / "services" / service / "uv.lock").read_text())
    (entry,) = [p for p in lock["package"] if p["name"] == package]
    source = entry["source"]
    commit = source["git"].rsplit("#", 1)[1] if "git" in source else None
    return str(entry["version"]), commit


@in_the_monorepo
def test_the_files_the_pins_are_read_from_are_where_the_tests_expect_them() -> None:
    for service in ("ocsr", "seg", "cls", "chem"):
        assert (REPO / "services" / service / "uv.lock").is_file(), service
    assert (REPO / "deploy" / "weights" / "SHA256SUMS").is_file()
    assert (REPO / "deploy" / "chem.Dockerfile").is_file()


@in_the_monorepo
@pytest.mark.parametrize(
    ("engine_id", "service", "package"),
    [
        ("decimer", "ocsr", "decimer"),
        ("decimer-segmentation", "seg", "decimer-segmentation"),
        ("decimer-image-classifier", "cls", "decimer-image-classifier"),
        ("orthonym", "chem", "orthonym"),
    ],
)
def test_the_listed_versions_are_the_ones_the_services_lock(
    engine_id: str, service: str, package: str
) -> None:
    """Bump a pin in a service and this fails until engines.py says the same."""
    version, commit = _locked(service, package)
    engine = next(e for e in MODEL_ENGINES if e.id == engine_id)
    assert (engine.version, engine.revision) == (version, commit)


OPSIN_JAR = re.compile(r"opsin-cli-(\d+(?:\.\d+)*)-jar-with-dependencies\.jar")


@in_the_monorepo
def test_the_opsin_version_is_the_jar_the_image_checks_and_installs() -> None:
    """OPSIN is not a Python package: its version is in the name of the jar that deploy/weights
    lists with its checksum and that deploy/chem.Dockerfile requires to be there."""
    opsin = next(e for e in MODEL_ENGINES if e.id == "opsin")
    sums = (REPO / "deploy" / "weights" / "SHA256SUMS").read_text()
    listed = [
        OPSIN_JAR.search(line.split(None, 1)[1])
        for line in sums.splitlines()
        if not line.startswith("#") and len(line.split(None, 1)) == 2 and "opsin-cli" in line
    ]
    assert len(listed) == 1 and listed[0] is not None, "one checksummed opsin jar in SHA256SUMS"
    assert listed[0].group(1) == opsin.version
    dockerfile = (REPO / "deploy" / "chem.Dockerfile").read_text()
    required = {m.group(1) for m in OPSIN_JAR.finditer(dockerfile)}
    assert required == {opsin.version}, "chem.Dockerfile requires the same jar"
