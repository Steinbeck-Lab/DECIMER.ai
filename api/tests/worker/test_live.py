"""Opt-in: the real pipeline against real, already running model services.

Skipped unless SEG_URL, CLS_URL and OCSR_URL are set (and CHEM_URL, optionally, for names). It
starts nothing: bring the services up yourself (see services/*/README.md), then

    SEG_URL=http://127.0.0.1:8001 OCSR_URL=http://127.0.0.1:8002 CLS_URL=http://127.0.0.1:8003 \
      uv run pytest -m slow tests/worker/test_live.py -s

It renders page 3 of the golden paper (Figure 2, 12 numbered structures; LIVE_PAGES="3,4" picks
other 1-based pages) with the production renderer (a child process at 300 DPI), runs seg, ocsr
and cls over HTTP, and checks the answers against `golden/labels.csv`. The count of labelled
structures recovered is printed, not asserted beyond "some": the pass rule of the golden-set
check (docs/ROADMAP.md §5) is the place for thresholds.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from decimer_api.models import Job, JobStatus, new_job_id
from decimer_api.settings import Settings
from decimer_api.store import Admission, JobStore
from decimer_api.worker.pipeline import run_pipeline
from decimer_api.worker.runtime import build_deps
from decimer_api.worker.settings import WorkerSettings

REPO = Path(__file__).resolve().parents[3]
GOLDEN_PDF = REPO / "golden" / "zou2024_penicillium_shentong.pdf"
LABELS = REPO / "golden" / "labels.csv"
REQUIRED = ("SEG_URL", "CLS_URL", "OCSR_URL")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        any(not os.environ.get(name) for name in REQUIRED),
        reason=f"set {', '.join(REQUIRED)} to run against real services",
    ),
    pytest.mark.skipif(not GOLDEN_PDF.is_file(), reason="golden/ is not in this checkout"),
]


def pages_pdf(pages: list[int]) -> bytes:
    """The given 1-based pages of the golden paper as a PDF."""
    source = pdfium.PdfDocument(GOLDEN_PDF)
    target = pdfium.PdfDocument.new()
    try:
        target.import_pages(source, pages=[page - 1 for page in pages])
        path = Path(os.environ.get("TMPDIR", "/tmp")) / f"live-{os.getpid()}.pdf"  # noqa: S108
        target.save(path)
        try:
            return path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
    finally:
        target.close()
        source.close()


async def test_the_real_services_recognise_the_structures_on_a_golden_page(
    settings: Settings, store: JobStore
) -> None:
    pages = [int(p) for p in os.environ.get("LIVE_PAGES", "3").split(",")]
    worker = WorkerSettings()  # SEG_URL, OCSR_URL, CLS_URL (and CHEM_URL) from the environment
    deps = build_deps(settings, worker, store)
    try:
        job = Job.create(
            job_id=new_job_id(),
            client_ip_hash="live",
            pages=len(pages),
            pages_processed=len(pages),
            now=store.now(),
        )
        (settings.data_dir / job.id / "input").mkdir(parents=True)
        (settings.data_dir / job.id / "input" / "document.pdf").write_bytes(pages_pdf(pages))
        assert await store.admit(job) is Admission.OK

        await run_pipeline(job.id, deps)
        done = await store.get(job.id, include_expired=True)
    finally:
        await deps.aclose()

    assert done is not None
    assert done.status is JobStatus.DONE, done.error
    assert done.results, "the services found no structure at all"
    folder = settings.data_dir / job.id
    for number in range(len(pages)):
        assert (folder / f"page-{number}.png").is_file()
    for result in done.results:
        assert (folder / f"crop-{result.index}.png").is_file()
        assert 0 <= result.confidence <= 1

    with LABELS.open(newline="") as handle:
        labelled = [row["inchikey"] for row in csv.DictReader(handle) if int(row["page"]) in pages]
    found = [r.inchikey for r in done.results if r.valid]
    matched = sum(1 for key in labelled if key in found)
    print(
        f"\nlive: pages {pages}: {len(done.results)} results, {len(found)} valid, "
        f"{matched} of {len(labelled)} labelled structures recovered, "
        f"{sum(1 for r in done.results if r.is_structure is False)} flagged by cls, "
        f"warnings: {[w.code for w in done.warnings]}"
    )
    assert matched >= 1
