"""The job pipeline: render, segment, recognise, check.

`run_pipeline(job_id, deps)` is everything the worker does with one job; the arq function
`process_job` and the in-process development mode are thin wrappers around it.

Contract with the API (see "Worker contract" in the project README):

* `store.mark_running` first; a job whose record is gone or already finished is skipped.
* `store.save` after every stage (and at least every `HEARTBEAT_SECONDS`), so the frontend can
  poll the stages and a running record never expires under a busy worker.
* `store.finish` exactly once, in a `finally`, whatever happened: done, a failure that is
  explained to the user, or a bug. The one exception is cancellation (arq stops the worker):
  arq runs a cancelled job again after the restart, so the job is left running and the next run
  starts from scratch.
* Files go flat into `DATA_DIR/<id>/`: `page-<n>.png` and `crop-<n>.png`; the input under
  `input/` is only read.

What goes wrong, and what the user gets:

* a page cannot be rendered: it is skipped, warning `page_render_failed`; no page at all: the
  job fails, `render_failed`;
* seg or ocsr is unreachable or busy for good, or ocsr times out `OCSR_MAX_TIMEOUTS` times: the
  job fails, `service_unavailable`;
* seg refuses, crashes or times out on one page (or its image cannot be fitted to seg's limits):
  that page is skipped, warning `page_segmentation_failed`; on every page: the job fails,
  `segmentation_failed`;
* ocsr refuses, crashes or times out on one crop: that result is invalid, warning
  `recognition_failed`;
* cls refuses or crashes on one crop: `isStructure` is null, warning `classification_failed`;
  cls unreachable or timing out: no more calls to it in this job, null for the crops it did not
  answer, one warning `classifier_unavailable`;
* chem failing, timing out or declining for time: names stay null, one warning
  `names_unavailable` (a decline for any other reason is just "no name");
* the prediction had extra fragments (legacy's rule, D16): the first is kept, warning
  `fragments_trimmed`;
* anything unexpected: the job fails, `internal_error`; it runs too long: `timeout`.

Memory: the crops of a page are written to part files as the page's answer arrives and only the
first `MAX_STRUCTURES` of a page are kept; a crop is read back when it is recognised. Pages that
exceed seg's limits are sent as a smaller copy and cut from the full page (`crops.py`).

A failed job's `error` is fixed English text and the same text goes into `warnings` with the
failure's code. Nothing from inside (exception text, paths, what a service said) reaches the
record; it goes to the log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..chemistry import Chemistry, analyse_smiles
from ..models import (
    Job,
    JobStatus,
    JobWarning,
    Result,
    StageName,
    StageState,
    initial_stages,
    is_valid_job_id,
)
from ..settings import Settings
from ..store import JobStore
from .crops import PART_PATTERN, CropRef, PageParts, Scaling, write_parts
from .errors import (
    INPUT_MISSING,
    INTERNAL_ERROR,
    RENDER_FAILED,
    SEGMENTATION_FAILED,
    SERVICE_UNAVAILABLE,
    TIMEOUT,
    Failure,
    PipelineFailure,
    RenderError,
    ServiceError,
    ServiceTimedOut,
    ServiceUnavailable,
)
from .files import Fitted, fit_png, link_or_copy, png_file_size
from .fragments import trim_fragments
from .render import Renderer
from .services import OcsrAnswer, Services
from .settings import WorkerSettings

log = logging.getLogger("decimer_api.worker")

CdkFallback = Callable[[str], Awaitable[Chemistry | None]]

IMAGE_INPUT = re.compile(r"[0-9]{4}\.png")
# A structure's name, the tier it came in and how chem checked it; all three are None together.
Name = tuple[str | None, str | None, str | None]
NO_NAME: Name = (None, None, None)
CHEMISTRY_THREADS = 4  # structures analysed with RDKit at once (each in its own thread)
CHEM_MAX_SMILES = 2000  # the longest SMILES chem accepts
# Warnings the worker adds; a job that runs again starts without them.
WORKER_WARNING_CODES = frozenset(
    {
        "structures_truncated",
        "page_render_failed",
        "page_segmentation_failed",
        "fragments_trimmed",
        "recognition_failed",
        "classification_failed",
        "classifier_unavailable",
        "names_unavailable",
        INPUT_MISSING.code,
        RENDER_FAILED.code,
        SEGMENTATION_FAILED.code,
        SERVICE_UNAVAILABLE.code,
        TIMEOUT.code,
        INTERNAL_ERROR.code,
    }
)


@dataclass
class Deps:
    """Everything a pipeline run uses, built once per worker process (`runtime.build_deps`)."""

    settings: Settings
    worker: WorkerSettings
    store: JobStore
    services: Services
    renderer: Renderer
    gates: Mapping[str, asyncio.Semaphore]
    # D4: the CDK fallback. Plugged in only when the golden-set data keeps CDK; it is called for
    # a SMILES RDKit rejects, and only when `worker.cdk_fallback` is on.
    cdk_fallback: CdkFallback | None = None
    # The shared HTTP client, when `build_deps` opened it: `aclose` closes it.
    http: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()


@dataclass(frozen=True)
class Page:
    index: int
    path: Path
    width: int
    height: int


@dataclass(frozen=True)
class Crop:
    index: int  # the result index, 0-based, in reading order across pages
    page_index: int
    bbox: tuple[int, int, int, int]
    path: Path  # crop-<index>.png in the job folder; read when the crop is recognised


@dataclass(frozen=True)
class Recognition:
    smiles: str | None  # None: ocsr gave no answer for this crop
    confidence: float
    is_structure: bool | None  # None: cls gave no answer


@dataclass
class _Inputs:
    pdf: Path | None
    images: list[Path] = field(default_factory=list)


# --- entry point -------------------------------------------------------------------------------


async def run_pipeline(job_id: str, deps: Deps) -> None:
    """Run one job. Returns normally when the job is finished, whether done or failed."""
    if not is_valid_job_id(job_id):  # never build a path from anything else
        log.error("refusing job id %r", job_id)
        return
    store = deps.store
    existing = await store.get(job_id)
    if existing is None or existing.status.terminal:
        log.info("job %s: nothing to do (expired or already finished)", job_id)
        return
    job = await store.mark_running(job_id)
    if job is None:
        return
    interrupted = False
    timer = asyncio.timeout(deps.worker.pipeline_timeout_seconds)
    try:
        _reset(job)
        run = _Run(job, deps)
        async with timer:
            async with run.heartbeat():
                await run.execute()
        job.status = JobStatus.DONE
    except PipelineFailure as failure:
        log.warning("job %s failed: %s", job_id, failure)
        _fail(job, failure.failure)
    except TimeoutError:
        if timer.expired():
            log.warning(
                "job %s ran for more than %.0f s", job_id, deps.worker.pipeline_timeout_seconds
            )
            _fail(job, TIMEOUT)
        else:
            log.exception("job %s: unexpected timeout", job_id)
            _fail(job, INTERNAL_ERROR)
    except Exception:
        log.exception("job %s failed unexpectedly", job_id)
        _fail(job, INTERNAL_ERROR)
    except BaseException:
        # Cancelled (the worker is stopping) or interrupted: arq runs the job again, so it is
        # neither finished nor marked failed.
        interrupted = True
        raise
    finally:
        if not interrupted:
            await store.finish(job)


def _reset(job: Job) -> None:
    """Start from the state the API created, so that a second run inherits nothing."""
    job.stages = initial_stages()
    job.results = []
    job.structures_found = 0
    job.structures_dropped = 0
    job.truncated = job.pages_processed < job.pages  # the API's page limit; ours is set later
    job.error = None
    job.warnings = [w for w in job.warnings if w.code not in WORKER_WARNING_CODES]


def _fail(job: Job, failure: Failure) -> None:
    job.status = JobStatus.FAILED
    job.error = failure.message
    job.warnings.append(JobWarning(code=failure.code, message=failure.message))


# --- one run -------------------------------------------------------------------------------------


class _Run:
    def __init__(self, job: Job, deps: Deps) -> None:
        self.job = job
        self.deps = deps
        self.worker = deps.worker
        self.services = deps.services
        self.folder = deps.settings.data_dir / job.id
        self.down: set[str] = set()  # optional services found dead or stuck during this job
        self.ocsr_timeouts = 0
        self.names_warned = False
        self.single_image = False
        self.pages: list[Page] = []
        self.crops: list[Crop] = []
        self.recognitions: list[Recognition] = []
        # One save at a time: the heartbeat and a stage can both want to save, and the record
        # written last must be the one saved last.
        self._saving = asyncio.Lock()

    # --- bookkeeping ---

    def warn(self, code: str, message: str) -> None:
        self.job.warnings.append(JobWarning(code=code, message=message))

    async def save(self) -> None:
        async with self._saving:
            await self.deps.store.save(self.job)

    def _set_stage(self, name: StageName, state: StageState) -> None:
        for stage in self.job.stages:
            if stage.name is name:
                stage.state = state

    async def _stage(self, name: StageName, work: Callable[[], Awaitable[None]]) -> None:
        self._set_stage(name, StageState.ACTIVE)
        await self.save()
        await work()
        self._set_stage(name, StageState.DONE)
        await self.save()

    @contextlib.asynccontextmanager
    async def heartbeat(self) -> AsyncIterator[None]:
        """Save the record every `heartbeat_seconds` while the body runs, so that a long stage
        cannot let it expire. Stopped before the body's caller finishes the job."""

        async def beat() -> None:
            while True:
                await asyncio.sleep(self.worker.heartbeat_seconds)
                try:
                    await self.save()
                except Exception:
                    log.warning("job %s: heartbeat save failed", self.job.id, exc_info=True)

        task = asyncio.create_task(beat())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def execute(self) -> None:
        await self._stage(StageName.RENDER, self._render)
        await self._stage(StageName.SEGMENT, self._segment)
        await self._stage(StageName.RECOGNISE, self._recognise)
        await self._stage(StageName.CHECK, self._check)

    # --- render ---

    async def _render(self) -> None:
        inputs = await asyncio.to_thread(_find_inputs, self.folder / "input")
        pages: list[Page | None]
        if inputs.pdf is not None:
            pdf = inputs.pdf
            pages = await _map(
                range(self.job.pages_processed),
                lambda index: self._render_pdf_page(pdf, index),
                limit=self.worker.render_concurrency,
            )
        else:
            pages = await _map(
                list(enumerate(inputs.images)),
                lambda item: self._copy_image(*item),
                limit=self.worker.render_concurrency,
            )
            self.single_image = len(inputs.images) == 1
        self.pages = [page for page in pages if page is not None]
        if not self.pages:
            raise PipelineFailure(RENDER_FAILED)

    def _page_not_rendered(self, index: int, error: Exception) -> None:
        log.warning("job %s: page %d: %s", self.job.id, index, error)
        self.warn("page_render_failed", f"Page {index + 1} could not be rendered and was skipped.")

    async def _render_pdf_page(self, pdf: Path, index: int) -> Page | None:
        out = self.folder / f"page-{index}.png"
        try:
            async with self.deps.gates["render"]:
                width, height = await self.deps.renderer(pdf, index, out)
        except RenderError as error:
            self._page_not_rendered(index, error)
            return None
        return Page(index, out, width, height)

    async def _copy_image(self, index: int, source: Path) -> Page | None:
        out = self.folder / f"page-{index}.png"

        def work() -> tuple[int, int]:
            link_or_copy(source, out)
            return png_file_size(out)

        try:
            width, height = await asyncio.to_thread(work)
        except (OSError, ValueError) as error:
            self._page_not_rendered(index, error)
            return None
        return Page(index, out, width, height)

    # --- segment ---

    async def _segment(self) -> None:
        await asyncio.to_thread(_remove_parts, self.folder)  # left by a run that was cancelled
        outcomes = await _map(self.pages, self._segment_page, limit=self.worker.seg_concurrency)
        if all(outcome is None for outcome in outcomes):
            raise PipelineFailure(SEGMENTATION_FAILED)

        parts = [outcome for outcome in outcomes if outcome is not None]
        total = sum(part.found for part in parts)
        refs = [ref for part in parts for ref in part.refs]
        cap = self.deps.settings.max_structures
        if total == 0 and self.single_image:
            # Only one uploaded image, and nothing found on it: the image is the structure.
            self.crops = await asyncio.to_thread(self._whole_page_crop)
            total = 1
        else:
            self.crops = await asyncio.to_thread(_finalise, self.folder, refs, cap)
        self.job.structures_found = total
        self.job.structures_dropped = total - len(self.crops)
        if self.job.structures_dropped:
            self.job.truncated = True
            self.warn(
                "structures_truncated",
                f"{total} structures were found. Only the first {len(self.crops)} were processed.",
            )

    def _whole_page_crop(self) -> list[Crop]:
        """The single-image fallback: the crop is another name for the page file (a hard link)."""
        page = self.pages[0]
        path = self.folder / "crop-0.png"
        link_or_copy(page.path, path)
        return [Crop(0, page.index, (0, 0, page.width, page.height), path)]

    async def _segment_page(self, page: Page) -> PageParts | None:
        settings = self.worker

        def prepare() -> Fitted:
            return fit_png(
                page.path.read_bytes(),
                max_pixels=settings.seg_input_max_pixels,
                max_bytes=settings.seg_input_max_bytes,
            )

        try:
            fitted = await asyncio.to_thread(prepare)
        except (OSError, ValueError) as error:  # ImageTooLarge is a ValueError
            self._page_not_searched(page, error)
            return None
        try:
            answer = await self.services.segment(fitted.data, page.index)
        except ServiceUnavailable as error:
            log.warning("job %s: %s", self.job.id, error)
            raise PipelineFailure(SERVICE_UNAVAILABLE) from error
        except ServiceError as error:
            self._page_not_searched(page, error)
            return None
        # What seg found goes to disk now, a page at a time. When seg was sent a smaller copy
        # its boxes are scaled to the page and the crops are cut from the page itself.
        scaling = (
            Scaling(page.path, (fitted.width, fitted.height), (page.width, page.height))
            if fitted.scaled
            else None
        )
        segments = sorted(answer.segments, key=lambda segment: segment.index)
        return await asyncio.to_thread(
            write_parts,
            self.folder,
            page_index=page.index,
            segments=segments,
            keep=self.deps.settings.max_structures,
            source=scaling,
        )

    def _page_not_searched(self, page: Page, error: Exception) -> None:
        log.warning("job %s: page %d: %s", self.job.id, page.index, error)
        self.warn(
            "page_segmentation_failed",
            f"Page {page.index + 1} could not be searched for structures.",
        )

    # --- recognise ---

    async def _recognise(self) -> None:
        limit = max(self.worker.ocsr_concurrency, self.worker.cls_concurrency)
        self.recognitions = await _map(self.crops, self._recognise_crop, limit=limit)

    async def _recognise_crop(self, crop: Crop) -> Recognition:
        number = crop.index + 1
        settings = self.worker

        def prepare() -> bytes:
            return fit_png(
                crop.path.read_bytes(),
                max_pixels=settings.model_input_max_pixels,
                max_side=settings.model_input_max_side,
                max_bytes=settings.model_input_max_bytes,
            ).data

        try:
            png = await asyncio.to_thread(prepare)
        except (OSError, ValueError) as error:
            log.warning("job %s: structure %d: %s", self.job.id, number, error)
            self.warn("recognition_failed", f"Structure {number} could not be recognised.")
            return Recognition(smiles=None, confidence=0.0, is_structure=None)
        ocsr, is_structure = await _both(self._ocsr(crop, png), self._classify(crop, png))
        if ocsr is None:
            return Recognition(smiles=None, confidence=0.0, is_structure=is_structure)
        return Recognition(
            smiles=self._without_extra_fragments(number, ocsr.smiles),
            confidence=ocsr.confidence,
            is_structure=is_structure,
        )

    def _without_extra_fragments(self, number: int, smiles: str) -> str:
        """Legacy's rule. The raw prediction is in the debug log only."""
        trimmed = trim_fragments(smiles)
        if trimmed != smiles:
            log.debug(
                "job %s: structure %d: %r trimmed to %r", self.job.id, number, smiles, trimmed
            )
            self.warn(
                "fragments_trimmed",
                f"Structure {number}: the prediction had several fragments. "
                "Only the first was kept.",
            )
        return trimmed

    async def _ocsr(self, crop: Crop, png: bytes) -> OcsrAnswer | None:
        number = crop.index + 1
        try:
            return await self.services.recognise(png)
        except ServiceUnavailable as error:
            log.warning("job %s: %s", self.job.id, error)
            raise PipelineFailure(SERVICE_UNAVAILABLE) from error
        except ServiceTimedOut as error:
            self.ocsr_timeouts += 1
            log.warning(
                "job %s: structure %d: %s (timeout %d of %d)",
                self.job.id,
                number,
                error,
                self.ocsr_timeouts,
                self.worker.ocsr_max_timeouts,
            )
            if self.ocsr_timeouts >= self.worker.ocsr_max_timeouts:
                raise PipelineFailure(SERVICE_UNAVAILABLE) from error
        except ServiceError as error:
            log.warning("job %s: structure %d: %s", self.job.id, number, error)
        self.warn("recognition_failed", f"Structure {number} could not be recognised.")
        return None

    async def _classify(self, crop: Crop, png: bytes) -> bool | None:
        if "cls" in self.down:
            return None
        number = crop.index + 1
        try:
            return (await self.services.classify(png)).is_structure
        except (ServiceUnavailable, ServiceTimedOut) as error:
            log.warning("job %s: %s", self.job.id, error)
            if "cls" not in self.down:
                self.down.add("cls")
                self.warn(
                    "classifier_unavailable",
                    "The classifier is unavailable, so the structures were not checked.",
                )
        except ServiceError as error:
            log.warning("job %s: structure %d: %s", self.job.id, number, error)
            self.warn("classification_failed", f"Structure {number} could not be classified.")
        return None

    # --- check ---

    async def _check(self) -> None:
        pairs = list(zip(self.crops, self.recognitions, strict=True))
        chemistry = await _map(pairs, self._analyse, limit=CHEMISTRY_THREADS)
        names = await self._names(chemistry)
        self.job.results = [
            Result(
                index=crop.index,
                page_index=crop.page_index,
                bbox=crop.bbox,
                smiles=chem.smiles,
                inchikey=chem.inchikey,
                valid=chem.valid,
                is_structure=recognition.is_structure,
                confidence=recognition.confidence,
                name=name,
                name_tier=tier,
                name_verified=verified,
                svg=chem.svg,
                molfile=chem.molfile,
            )
            for (crop, recognition), chem, (name, tier, verified) in zip(
                pairs, chemistry, names, strict=True
            )
        ]

    async def _analyse(self, item: tuple[Crop, Recognition]) -> Chemistry:
        recognition = item[1]
        if recognition.smiles is None:
            return Chemistry(smiles="", inchikey="", valid=False, molfile="", svg="")
        chemistry = await asyncio.to_thread(analyse_smiles, recognition.smiles)
        fallback = self.deps.cdk_fallback
        if chemistry.valid or fallback is None or not self.worker.cdk_fallback:
            return chemistry
        try:
            return await fallback(recognition.smiles) or chemistry
        except Exception:
            log.warning("job %s: CDK fallback failed", self.job.id, exc_info=True)
            return chemistry

    async def _names(self, chemistry: list[Chemistry]) -> list[Name]:
        """IUPAC names from chem, one batch per `chem_batch_size` structures (a job has 20, so
        one request). A structure that is invalid, has a dummy atom (an R group) or is too long
        for chem is not asked about. Any failure, and a structure chem ran out of time on, leaves
        the names empty and gives one warning; the job is never failed by it."""
        names: list[Name] = [NO_NAME] * len(chemistry)
        if not self.services.chem_enabled:
            return names
        asked = [
            index
            for index, chem in enumerate(chemistry)
            if chem.valid and "*" not in chem.smiles and len(chem.smiles) <= CHEM_MAX_SMILES
        ]
        size = self.worker.chem_batch_size
        for start in range(0, len(asked), size):
            batch = asked[start : start + size]
            try:
                answers = await self.services.names([chemistry[index].smiles for index in batch])
            except ServiceError as error:
                log.warning("job %s: %s", self.job.id, error)
                self._names_unavailable("IUPAC names are unavailable at the moment.")
                break
            for index, answer in zip(batch, answers, strict=True):
                if answer.name:
                    names[index] = (answer.name, answer.tier, answer.verified)
                elif answer.reason == "timeout":
                    self._names_unavailable("Some IUPAC names could not be computed in time.")
        return names

    def _names_unavailable(self, message: str) -> None:
        if not self.names_warned:
            self.names_warned = True
            self.warn("names_unavailable", message)


# --- helpers -------------------------------------------------------------------------------------


def _find_inputs(input_dir: Path) -> _Inputs:
    """What the API stored: `document.pdf`, or `0001.png`, `0002.png`, ... Raises
    `PipelineFailure` when there is nothing to work on."""
    pdf = input_dir / "document.pdf"
    if pdf.is_file() and not pdf.is_symlink():
        return _Inputs(pdf=pdf)
    try:
        names = sorted(p.name for p in input_dir.iterdir() if IMAGE_INPUT.fullmatch(p.name))
    except OSError:
        names = []
    images = [input_dir / name for name in names if not (input_dir / name).is_symlink()]
    if not images:
        raise PipelineFailure(INPUT_MISSING)
    return _Inputs(pdf=None, images=images)


def _remove_parts(folder: Path) -> None:
    for part in folder.glob(PART_PATTERN):
        part.unlink(missing_ok=True)


def _finalise(folder: Path, refs: list[CropRef], cap: int) -> list[Crop]:
    """Give the first `cap` part files their final names, `crop-<n>.png`, and delete the rest."""
    crops: list[Crop] = []
    for index, ref in enumerate(refs[:cap]):
        path = folder / f"crop-{index}.png"
        os.replace(ref.part, path)
        crops.append(Crop(index, ref.page_index, ref.bbox, path))
    for ref in refs[cap:]:
        ref.part.unlink(missing_ok=True)
    return crops


async def _map[T, R](
    items: Iterable[T], work: Callable[[T], Awaitable[R]], *, limit: int
) -> list[R]:
    """`work(item)` for every item, at most `limit` at a time, results in the order of `items`.
    The first failure cancels the rest and is raised as itself (a `PipelineFailure` wins)."""
    slots = asyncio.Semaphore(limit)

    async def one(item: T) -> R:
        async with slots:
            return await work(item)

    return await _gather([one(item) for item in items])


async def _gather[R](coroutines: Sequence[Coroutine[Any, Any, R]]) -> list[R]:
    tasks: list[asyncio.Task[R]] = []
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(coroutine) for coroutine in coroutines]
    except ExceptionGroup as failures:
        raise _chosen(failures) from None
    return [task.result() for task in tasks]


async def _both[A, B](first: Coroutine[Any, Any, A], second: Coroutine[Any, Any, B]) -> tuple[A, B]:
    """Run two calls together; if one fails the other is cancelled and the failure is raised."""
    try:
        async with asyncio.TaskGroup() as group:
            first_task = group.create_task(first)
            second_task = group.create_task(second)
    except ExceptionGroup as failures:
        raise _chosen(failures) from None
    return first_task.result(), second_task.result()


def _chosen(group: BaseExceptionGroup[BaseException]) -> BaseException:
    """The one failure to report from a task group: a `PipelineFailure` if there is one."""
    leaves = list(_leaves(group))
    return next((error for error in leaves if isinstance(error, PipelineFailure)), leaves[0])


def _leaves(group: BaseExceptionGroup[BaseException]) -> Iterator[BaseException]:
    for error in group.exceptions:
        if isinstance(error, BaseExceptionGroup):
            yield from _leaves(error)
        else:
            yield error
