"""The job pipeline: render, segment, recognise, check.

`run_pipeline(job_id, deps)` is everything the worker does with one job; the arq function
`process_job` and the in-process development mode are thin wrappers around it.

The stages OVERLAP: a page goes to seg as soon as it is rendered, and a crop goes to
ocsr and cls (and then to the RDKit check) as soon as its place in the job is known. The results
are exactly those of running the four stages one after the other:

* Crops are numbered in document order (page by page, seg's order within a page) and only the
  first `MAX_STRUCTURES` are recognised. A crop's index is the number of structures on all
  earlier pages plus its place on its page, so it is known only when every earlier page has been
  settled (segmented, failed or not rendered): the prefix rule of `overlap.PrefixNumbering`. A
  later page that seg answers first waits for its predecessors; nothing is ever renumbered.
* Every page is still segmented, also those beyond the cap, because `structuresFound` counts them.
* A job of exactly one uploaded image (one page: the numbering above is untouched) where seg finds
  exactly one structure covering at least `SINGLE_IMAGE_WHOLE_FRACTION` of the image recognises
  the whole image instead of seg's crop: the image is already a tight crop and seg's box can clip
  its edge. The result's box is then the whole image, as for the zero-structure fallback below.
* A job of SEVERAL uploaded images never calls seg (the earlier Laravel version of decimer.ai
  never segmented such an upload): each image is one structure, read whole by ocsr and cls,
  numbered in image order, and its box is the whole image. The images are numbered by the same
  prefix rule (an image "settles" as soon as it is in place) and by the same cap, and every image
  keeps its place, as in the earlier version: one that cannot be read (or whose crop cannot be
  written) is a result with an empty SMILES and the warning `recognition_failed`, and the images
  after it are not renumbered. The cap: the first `MAX_STRUCTURES` images are recognised,
  `structuresFound` counts them all, and the warning for more than that (`structures_truncated`)
  is raised. Such a job takes no seg slot and holds no seg ticket (its segment stage is active
  only until the numbering is done); its crops are fitted for the models like any other crop.
* The names (chem) are asked for in the same batches as before, once every structure is checked.
  So is CDK: once every structure is checked, the SMILES that RDKit refused go
  to chem's `POST /cdk` in one batch (chunks of at most 20), before the names are asked for. A
  structure that CDK accepts and RDKit refused keeps `valid=False` and gets `valid_by="cdk"`, its
  SMILES as DECIMER wrote it, CDK's molblock as its molfile and RDKit's drawing of that (read
  without sanitizing); it is not named (Orthonym needs RDKit's reading).
* `results` are filled in at once, when the check is done (never earlier). `structuresFound` is
  set when the last page has been segmented; a job that fails before that has 0.
* The warnings are kept in the order of the stages that raise them (`_WARNING_RANK`), as they
  read when the stages ran one after the other, not in the order things happened.

Contract with the API (see "Worker contract with the API" in the API README):

* `store.mark_running` first; a job whose record is gone or already finished is skipped.
* `store.save` whenever a stage changes state (and at least every `HEARTBEAT_SECONDS`), so the
  frontend can poll the stages and a running record never expires under a busy worker. Stages can
  be active at the same time. A stage turns active when its first piece of work starts and done
  when its last ends AND every stage before it is done (`overlap.StageTracker`).
* `store.finish` exactly once, in a `finally`, whatever happened: done, a failure that is
  explained to the user, or a bug. The one exception is cancellation (arq stops the worker):
  arq runs a cancelled job again after the restart, so the job is left running and the next run
  starts from scratch.
* Files go flat into `DATA_DIR/<id>/`: `page-<n>.png` and `crop-<n>.png`; the input under
  `input/` is only read.

What goes wrong, and what the user gets:

* a page cannot be rendered: it is skipped, warning `page_render_failed`; no page at all: the
  job fails, `render_failed` (not for a job of several images: there every image keeps its place,
  an unreadable one is a result that failed to be recognised);
* seg or ocsr is unreachable or busy for good, or ocsr times out `OCSR_MAX_TIMEOUTS` times: the
  job fails, `service_unavailable` (seg is not called for a job of several images);
* seg refuses, crashes or times out on one page (or its image cannot be fitted to seg's limits):
  that page is skipped, warning `page_segmentation_failed`; on every page: the job fails,
  `segmentation_failed`;
* ocsr refuses, crashes or times out on one crop: that result is invalid, warning
  `recognition_failed`;
* ocsr stopped at the model's token limit on one crop (it used to be refused with a 422): the
  cut-off SMILES is kept, as the earlier version kept it, and goes through the same checks as any
  other (RDKit, then CDK, so `valid` and `validBy` mean what they always mean) with
  `incomplete=true` on the result. No name is looked up for it (a checked name would read as
  "this is the structure in the image"); the log says so, with the structure's number and no
  SMILES. No warning: the flag is the result's own;
* cls refuses or crashes on one crop: `isStructure` is null, warning `classification_failed`;
  cls unreachable or timing out: no more calls to it in this job, null for the crops it did not
  answer, one warning `classifier_unavailable`;
* chem failing, timing out or declining for time: names stay null, one warning
  `names_unavailable` (a decline for any other reason is just "no name");
* chem failing for the CDK batch, or CDK running out of time or breaking on a structure, or chem
  sending a molblock that is no molfile: those structures stay invalid, one warning
  `cdk_unavailable`. After a TIMEOUT of that call chem is taken to be hung and the names are not
  asked for either (one `names_unavailable` if there was something to name). CDK declining a SMILES
  (it cannot read it or lay it out) is not a failure: the structure is invalid, as RDKit said; nor
  is a well-formed molblock that our molfile gate will not store (too many bonds at an atom, ...);
* the prediction had extra fragments (the earlier version's rule): the first is kept, warning
  `fragments_trimmed`;
* anything unexpected: the job fails, `internal_error`; it runs too long: `timeout`.

Several jobs run at once in one worker process and share the replicas of the model services.
Each job asks for as many calls at once as its service takes in all (`Services.capacity`); when
the replicas are busy the calls wait in the fair dispatcher of `replicas.py`, which serves small
jobs first and lets jobs take turns. The size class of a job is a rule over what the API stored
(`Settings.is_small_job(job.pages_processed)`).

Memory: the crops of a page are written to part files as the page's answer arrives and only the
first `MAX_STRUCTURES` of a page are kept; a crop is read back when it is recognised. Pages that
exceed seg's limits are sent as a smaller copy and cut from the full page (`crops.py`).

A failed job's `error` is fixed English text and the same text goes into `warnings` with the
failure's code. Nothing from inside (exception text, paths, what a service said) reaches the
record; it goes to the log.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import functools
import itertools
import logging
import os
import re
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Iterator,
    Mapping,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..chemistry import Chemistry, analyse_smiles, from_cdk
from ..models import (
    Job,
    JobStatus,
    JobWarning,
    Result,
    StageName,
    ValidBy,
    initial_stages,
    is_valid_job_id,
)
from ..molfile import MolfileError, MolfileTooBig
from ..names import MAX_SMILES_CHARS, nameable
from ..settings import Settings
from ..store import JobStore
from ..usage import NullUsage, Usage
from .crops import PART_PATTERN, CropRef, PageParts, Scaling, page_boxes, write_parts
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
    ServiceUnreachable,
)
from .files import Fitted, fit_png, link_or_copy, png_file_size
from .fragments import trim_fragments
from .overlap import PrefixNumbering, Released, StageTracker
from .render import Renderer
from .replicas import Lease
from .services import OcsrAnswer, Segment, Services
from .settings import WorkerSettings

log = logging.getLogger("decimer_api.worker")

IMAGE_INPUT = re.compile(r"[0-9]{4}\.png")
# A structure's name, the tier it came in and how chem checked it; all three are None together.
Name = tuple[str | None, str | None, str | None]
NO_NAME: Name = (None, None, None)
CHEMISTRY_THREADS = 4  # structures analysed with RDKit at once (each in its own thread)
# What chem takes as a SMILES (its request rule): printable ASCII, no space.
CHEM_SMILES = re.compile(rf"[\x21-\x7e]{{1,{MAX_SMILES_CHARS}}}")
# Why CDK has no molblock for a structure, when it is CDK's own business: it cannot read the SMILES
# or lay it out, or the structure is more than DECIMER can write. Any other reason (timeout, engine
# trouble) means CDK did not get to decide.
CDK_DECLINED = frozenset({"invalid_smiles", "layout_failed", "too_large"})
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
        "cdk_unavailable",
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
    # The shared HTTP client, when `build_deps` opened it: `aclose` closes it.
    http: httpx.AsyncClient | None = None
    # The per-day counts (`usage`): a finished job is counted once. Counts nothing unless given.
    usage: Usage = field(default_factory=NullUsage)

    async def aclose(self) -> None:
        await self.services.aclose()
        if self.http is not None:
            await self.http.aclose()


@dataclass(frozen=True)
class Page:
    index: int
    path: Path
    width: int
    height: int
    # False: the image could not be read (a job of several images): there is no page file
    # and no crop, and the size is unknown (0 x 0).
    readable: bool = True


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
    incomplete: bool = False  # ocsr stopped at its token limit: the SMILES is cut off


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
            await deps.usage.job_finished(
                failed=job.status is JobStatus.FAILED,
                pages=job.pages_processed,
                structures=len(job.results),
            )


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

# Where a warning goes among the others: by the stage that raises it, so that the list reads as it
# did when the stages ran one after the other (the API's own warnings, rank 0, come first).
_WARNING_RANK = {
    StageName.RENDER: 1,
    StageName.SEGMENT: 2,
    StageName.RECOGNISE: 3,
    StageName.CHECK: 4,
}

Render = Callable[[], Awaitable[Page | None]]


class _Limit:
    """At most `capacity()` pieces of work of one job at a time, where the capacity (the calls a
    service takes over all its replicas) is looked up each time a piece asks to start and each time
    one ends, not once for the run: a replica that joins while the job runs is used from the next
    piece on, as the dispatcher of `replicas.py` already does for the calls themselves. Waiting
    pieces usually start in the order they asked; a newcomer can take a slot between a wake-up
    and the woken piece's re-check (that only changes which page seg sees first, never a
    result)."""

    def __init__(self, capacity: Callable[[], Awaitable[int]]) -> None:
        self._capacity = capacity
        self._running = 0
        self._waiting: list[asyncio.Future[None]] = []

    @contextlib.asynccontextmanager
    async def __call__(self) -> AsyncIterator[None]:
        await self._enter()
        try:
            yield
        finally:
            self._running -= 1
            self._wake()

    async def _enter(self) -> None:
        while True:
            limit = await self._capacity()
            if self._running < limit:  # checked and counted with no await in between
                self._running += 1
                return
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiting.append(waiter)
            try:
                await waiter
            finally:
                if waiter in self._waiting:
                    self._waiting.remove(waiter)

    def _wake(self) -> None:
        waiting, self._waiting = self._waiting, []
        for waiter in waiting:
            if not waiter.done():
                waiter.set_result(None)


class _Run:
    def __init__(self, job: Job, deps: Deps) -> None:
        self.job = job
        self.deps = deps
        self.worker = deps.worker
        # The calls of this job are tagged with its id and size class: when the replicas are
        # busy, small jobs go first and jobs take turns (`replicas.py`).
        self.small = deps.settings.is_small_job(job.pages_processed)
        self.services = deps.services.for_job(job.id, self.small)
        self.folder = deps.settings.data_dir / job.id
        self.down: set[str] = set()  # optional services found dead or stuck during this job
        self.ocsr_timeouts = 0
        self.names_warned = False
        self.cdk_warned = False
        # A call to chem timed out, or chem could not be reached (a busy chem is neither: it
        # answers): it is not asked again in this job.
        self.chem_hung = False
        self.single_image = False
        self.whole_images = False  # several images: read whole, seg is never called
        self.crops: list[Crop] = []  # numbered crops, as they were released: result order
        self.recognitions: dict[int, Recognition] = {}  # by crop index
        self.chemistry: dict[int, Chemistry] = {}
        # One save at a time: the heartbeat and a stage can both want to save, and the record
        # written last must be the one saved last.
        self._saving = asyncio.Lock()
        self._stages = StageTracker()
        self._ranks = [0] * len(job.warnings)  # the rank of each warning, in step with the list
        # The overlap (see the module doc). `execute` sets the limits once it knows the replicas.
        self._group: asyncio.TaskGroup | None = None
        self._render_slots = asyncio.Semaphore(self.worker.render_concurrency)
        # As many pages of this job at seg as it takes in all (its replicas), as many crops at
        # ocsr and cls as the one of them with more room takes: no more, the rest of their queues
        # belongs to other jobs.
        self._seg_slots = _Limit(lambda: self.services.capacity("seg"))
        self._crop_slots = _Limit(self._crop_capacity)
        self._analysis_slots = asyncio.Semaphore(CHEMISTRY_THREADS)
        self._numbering = PrefixNumbering[CropRef](0, 0)
        self._ordering = asyncio.Lock()  # one page is released at a time
        self._pages: dict[int, Page] = {}  # rendered pages, by position
        self._rendering = 0  # pages whose render has not ended
        self._segmented = 0  # pages that have crops to number: seg answered, or read whole
        self._segmentation_done = False
        self._recognising = 0  # crops numbered whose recognition has not ended

    # --- bookkeeping ---

    def warn(self, stage: StageName, code: str, message: str) -> None:
        rank = _WARNING_RANK[stage]
        at = bisect.bisect_right(self._ranks, rank)
        self._ranks.insert(at, rank)
        self.job.warnings.insert(at, JobWarning(code=code, message=message))

    async def save(self) -> None:
        async with self._saving:
            await self.deps.store.save(self.job)

    async def _publish(self, *, force: bool = False) -> None:
        """Bring the stage states of the record up to date and save it if one changed (or
        `force`): a stage's change of state is when the frontend hears of it."""
        changed = self._stages.apply(self.job.stages)
        if changed or force:
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

    # --- the run ---

    async def execute(self) -> None:
        self._stages.start(StageName.RENDER)
        await self._publish()
        inputs = await asyncio.to_thread(_find_inputs, self.folder / "input")
        await asyncio.to_thread(_remove_parts, self.folder)  # left by a run that was cancelled
        renders = self._renders(inputs)
        if not renders:
            raise PipelineFailure(RENDER_FAILED)
        self._rendering = len(renders)
        self._numbering = PrefixNumbering(len(renders), self.deps.settings.max_structures)
        # One task group for everything in flight, so that a failure, the job's deadline or a
        # stopping worker cancels all of it, and the `async with` blocks give every slot back.
        try:
            async with asyncio.TaskGroup() as group:
                self._group = group
                for position, render in enumerate(renders):
                    group.create_task(self._page(position, render))
        except ExceptionGroup as failures:
            raise _chosen(failures) from None
        await self._finish_check()

    async def _crop_capacity(self) -> int:
        return max(await self.services.capacity("ocsr"), await self.services.capacity("cls"))

    def _renders(self, inputs: _Inputs) -> list[Render]:
        if inputs.pdf is not None:
            pdf = inputs.pdf
            return [
                functools.partial(self._render_pdf_page, pdf, index)
                for index in range(self.job.pages_processed)
            ]
        self.single_image = len(inputs.images) == 1
        self.whole_images = len(inputs.images) > 1
        return [
            functools.partial(self._copy_image, index, source)
            for index, source in enumerate(inputs.images)
        ]

    async def _page(self, position: int, render: Render) -> None:
        """One page from start to finish: render it, have seg search it, settle its place."""
        async with self._render_slots:
            page = await render()
        self._rendering -= 1
        if page is not None:
            self._pages[position] = page
            # Also for several images: the stage ends with the numbering.
            self._stages.start(StageName.SEGMENT)
        if self._rendering == 0:
            if not self._pages:
                raise PipelineFailure(RENDER_FAILED)
            self._stages.finish(StageName.RENDER)
        await self._publish()
        parts: PageParts | None = None
        if page is not None and self.whole_images:
            parts = await self._read_whole(page)
        elif page is not None:
            async with self._seg_slots():
                parts = await self._segment_page(page)
        await self._settle(position, parts)

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        if self._group is None:
            raise RuntimeError("no task group: only the pages in flight can send a crop on")
        self._group.create_task(work)

    # --- render ---

    def _page_not_rendered(self, index: int, error: Exception) -> None:
        log.warning("job %s: page %d: %s", self.job.id, index, error)
        self.warn(
            StageName.RENDER,
            "page_render_failed",
            f"Page {index + 1} could not be rendered and was skipped.",
        )

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
            if self.whole_images:
                # As in the earlier version: an unreadable image keeps its place (an empty result,
                # its warning comes when its crop is looked for). It gets no page file and no crop
                # (a link to bytes that are no PNG would be served as one), and its size is
                # unknown: a zero box.
                log.warning("job %s: image %d: %s", self.job.id, index + 1, error)
                await asyncio.to_thread(out.unlink, missing_ok=True)
                return Page(index, out, 0, 0, readable=False)
            self._page_not_rendered(index, error)
            return None
        return Page(index, out, width, height)

    # --- segment ---

    async def _segment_page(self, page: Page) -> PageParts | None:
        settings = self.worker

        def prepare() -> Fitted:
            return fit_png(
                page.path.read_bytes(),
                max_pixels=settings.seg_input_max_pixels,
                max_bytes=settings.seg_input_max_bytes,
            )

        # The slot first, the page after: a page is read, decoded and held as a PNG of up to
        # 36 MiB only while it has a seg replica to go to, so the pages in memory (and the threads
        # decoding them) are at most the slots of seg, whatever the number of waiting jobs.
        async with self.services.slot("seg") as lease:
            try:
                fitted = await asyncio.to_thread(prepare)
            except (OSError, ValueError) as error:  # ImageTooLarge is a ValueError
                self._page_not_searched(page, error)
                return None
            try:
                answer = await self.services.segment(fitted.data, page.index, lease=lease)
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
            del fitted  # the image that was sent is not needed any more
        segments = sorted(answer.segments, key=lambda segment: segment.index)
        if self.single_image:
            # Decided before seg's crops are cut, so that a fired rule never decodes and encodes
            # a crop of a big page it is going to replace with the page itself.
            whole = await asyncio.to_thread(
                self._whole_image_if_it_fills_the_frame, page, segments, scaling
            )
            if whole is not None:
                return whole
        return await asyncio.to_thread(
            write_parts,
            self.folder,
            page_index=page.index,
            segments=segments,
            keep=self.deps.settings.max_structures,
            source=scaling,
        )

    def _whole_image_if_it_fills_the_frame(
        self, page: Page, segments: list[Segment], scaling: Scaling | None
    ) -> PageParts | None:
        """A pasted image that is already a tight crop: seg's box is expanded from its mask and can
        clip the edge of the drawing (a methoxy group, a ring), so the whole image is sent to
        ocsr and cls instead. Only when seg found exactly one structure and its box covers at least
        `SINGLE_IMAGE_WHOLE_FRACTION` of the image; anything else (None) keeps what seg cut out.
        The box reported is then the whole image, as for the zero-structure fallback. Synchronous
        (a thread): it links a file."""
        boxes = page_boxes(segments, scaling)
        if len(boxes) != 1:  # found, not kept: with MAX_STRUCTURES=1 two found are still two
            return None
        ((number, (x0, y0, x1, y1)),) = boxes
        covered = (x1 - x0) * (y1 - y0)
        if covered < self.worker.single_image_whole_fraction * page.width * page.height:
            return None
        log.debug(
            "job %s: the one structure covers %.0f%% of the image: recognised whole",
            self.job.id,
            100 * covered / (page.width * page.height),
        )
        return self._whole_parts(page, number)

    def _whole_parts(self, page: Page, number: int) -> PageParts:
        """The page itself as the one structure on it, box and all: its part file is another name
        for the page file (a hard link), which `_finalise` renames to crop-<n>.png. Synchronous (a
        thread)."""
        part = self.folder / f"seg-{page.index}-{number}.part"
        link_or_copy(page.path, part)
        return PageParts(
            found=1, refs=[CropRef(page_index=page.index, bbox=_whole(page), part=part)]
        )

    async def _read_whole(self, page: Page) -> PageParts:
        """An image of a job of several images is one structure, read whole. Nothing is read
        or sent here (the crop is a link to the image); ocsr and cls get it later, under their
        slots, fitted like any crop. An image whose crop cannot be written keeps its place all the
        same: it has no crop file, so its recognition fails (`recognition_failed`), as the earlier
        version gives an image it cannot read its place with an empty SMILES."""
        if page.readable:
            try:
                return await asyncio.to_thread(self._whole_parts, page, 0)
            except OSError as error:
                log.warning(
                    "job %s: image %d: its crop cannot be written: %s",
                    self.job.id,
                    page.index + 1,
                    error,
                )
        part = self.folder / f"seg-{page.index}-0.part"  # never written: see `CropRef.written`
        return PageParts(
            found=1,
            refs=[CropRef(page_index=page.index, bbox=_whole(page), part=part, written=False)],
        )

    def _page_not_searched(self, page: Page, error: Exception) -> None:
        log.warning("job %s: page %d: %s", self.job.id, page.index, error)
        self.warn(
            StageName.SEGMENT,
            "page_segmentation_failed",
            f"Page {page.index + 1} could not be searched for structures.",
        )

    async def _settle(self, position: int, parts: PageParts | None) -> None:
        """A page is done (a page that failed has no `parts`). Its crops get their places, and
        are sent on to recognition, once every earlier page is done as well (the prefix rule)."""
        if parts is not None:
            self._segmented += 1
        found = parts.found if parts is not None else 0
        refs = parts.refs if parts is not None else []
        async with self._ordering:
            for released in self._numbering.settle(position, found, refs):
                await self._release(released)
            if self._numbering.complete:
                await self._segmentation_complete()

    async def _release(self, released: Released[CropRef]) -> None:
        crops = await asyncio.to_thread(_finalise, self.folder, released)
        for crop in crops:
            self._send_on(crop)

    def _send_on(self, crop: Crop) -> None:
        self.crops.append(crop)
        self._recognising += 1
        self._spawn(self._crop(crop))

    async def _segmentation_complete(self) -> None:
        """Every page is segmented, failed or skipped: what seg found in all is known."""
        if self._segmented == 0:
            raise PipelineFailure(SEGMENTATION_FAILED)
        total, kept = self._numbering.total, self._numbering.kept
        if total == 0 and self.single_image:
            # Only one uploaded image, and nothing found on it: the image is the structure.
            self._send_on(await asyncio.to_thread(self._whole_page_crop))
            total = kept = 1
        self.job.structures_found = total
        self.job.structures_dropped = total - kept
        if self.job.structures_dropped:
            self.job.truncated = True
            self.warn(
                StageName.SEGMENT,
                "structures_truncated",
                f"{total} structures were found. Only the first {kept} were processed.",
            )
        self._segmentation_done = True
        self._stages.finish(StageName.SEGMENT)
        self._recognition_may_be_done()
        await self._publish(force=True)  # always: the structure count first reaches the record here

    def _whole_page_crop(self) -> Crop:
        """The single-image fallback: the crop is another name for the page file (a hard link)."""
        page = self._pages[0]
        path = self.folder / "crop-0.png"
        link_or_copy(page.path, path)
        return Crop(0, page.index, _whole(page), path)

    # --- recognise and check, a crop at a time ---

    def _recognition_may_be_done(self) -> None:
        if self._segmentation_done and self._recognising == 0:
            self._stages.finish(StageName.RECOGNISE)

    async def _crop(self, crop: Crop) -> None:
        """One crop from its place to its check: ocsr and cls, then RDKit."""
        async with self._crop_slots():
            self._stages.start(StageName.RECOGNISE)
            await self._publish()
            recognition = await self._recognise_crop(crop)
        self.recognitions[crop.index] = recognition
        self._recognising -= 1
        self._recognition_may_be_done()
        await self._publish()
        async with self._analysis_slots:
            self._stages.start(StageName.CHECK)
            await self._publish()
            self.chemistry[crop.index] = await self._analyse(recognition)

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

        # The ocsr slot first, the crop after (as for seg pages): the crops in memory are at most
        # the slots of ocsr. cls takes its own slot for the same image, a moment later.
        async with self.services.slot("ocsr") as lease:
            try:
                png = await asyncio.to_thread(prepare)
            except (OSError, ValueError) as error:
                log.warning("job %s: structure %d: %s", self.job.id, number, error)
                self._not_recognised(number)
                return Recognition(smiles=None, confidence=0.0, is_structure=None)
            ocsr, is_structure = await _both(
                self._ocsr(crop, png, lease), self._classify(crop, png)
            )
        if ocsr is None:
            return Recognition(smiles=None, confidence=0.0, is_structure=is_structure)
        if ocsr.incomplete:
            # No SMILES in the log (the line is about the model, not about what it wrote).
            log.info(
                "job %s: structure %d: the prediction stopped at the token limit (incomplete)",
                self.job.id,
                number,
            )
        return Recognition(
            smiles=self._without_extra_fragments(number, ocsr.smiles),
            confidence=ocsr.confidence,
            is_structure=is_structure,
            incomplete=ocsr.incomplete,
        )

    def _without_extra_fragments(self, number: int, smiles: str) -> str:
        """The earlier version's rule. The raw prediction is in the debug log only."""
        trimmed = trim_fragments(smiles)
        if trimmed != smiles:
            log.debug(
                "job %s: structure %d: %r trimmed to %r", self.job.id, number, smiles, trimmed
            )
            self.warn(
                StageName.RECOGNISE,
                "fragments_trimmed",
                f"Structure {number}: the prediction had several fragments. "
                "Only the first was kept.",
            )
        return trimmed

    async def _ocsr(self, crop: Crop, png: bytes, lease: Lease) -> OcsrAnswer | None:
        number = crop.index + 1
        try:
            return await self.services.recognise(png, lease=lease)
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
        self._not_recognised(number)
        return None

    def _not_recognised(self, number: int) -> None:
        self.warn(
            StageName.RECOGNISE,
            "recognition_failed",
            f"Structure {number} could not be recognised.",
        )

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
                    StageName.RECOGNISE,
                    "classifier_unavailable",
                    "The classifier is unavailable, so the structures were not checked.",
                )
        except ServiceError as error:
            log.warning("job %s: structure %d: %s", self.job.id, number, error)
            self.warn(
                StageName.RECOGNISE,
                "classification_failed",
                f"Structure {number} could not be classified.",
            )
        return None

    # --- check ---

    async def _analyse(self, recognition: Recognition) -> Chemistry:
        if recognition.smiles is None:
            return Chemistry(smiles="", inchikey="", valid=False, molfile="", svg="")
        return await asyncio.to_thread(analyse_smiles, recognition.smiles)

    async def _finish_check(self) -> None:
        """Every crop is recognised and checked: name them, fill in the results, finish."""
        crops = sorted(self.crops, key=lambda crop: crop.index)
        chemistry = [self.chemistry[crop.index] for crop in crops]
        valid_by: list[ValidBy | None] = ["rdkit" if chem.valid else None for chem in chemistry]
        for position, accepted in (await self._cdk(chemistry)).items():
            chemistry[position] = accepted
            valid_by[position] = "cdk"
        names = await self._names(
            chemistry, [self.recognitions[crop.index].incomplete for crop in crops]
        )
        self.job.results = [
            Result(
                index=crop.index,
                page_index=crop.page_index,
                bbox=crop.bbox,
                smiles=chem.smiles,
                inchikey=chem.inchikey,
                valid=chem.valid,
                valid_by=by,
                is_structure=self.recognitions[crop.index].is_structure,
                confidence=self.recognitions[crop.index].confidence,
                incomplete=self.recognitions[crop.index].incomplete,
                name=name,
                name_tier=tier,
                name_verified=verified,
                svg=chem.svg,
                molfile=chem.molfile,
            )
            for crop, chem, by, (name, tier, verified) in zip(
                crops, chemistry, valid_by, names, strict=True
            )
        ]
        # No await between the results and the stage: a save never shows one without the other.
        self._stages.finish(StageName.CHECK)
        await self._publish()

    async def _cdk(self, chemistry: list[Chemistry]) -> dict[int, Chemistry]:
        """The structures of the job that RDKit refused, tried with CDK through chem, in ONE
        batch (a job has 20 structures at most, so one request; `chem_batch_size` per request when
        more). One call per structure would meet chem's gate (one running, a few waiting, then
        503). Returns the chemistry of the ones CDK accepted, by position. A SMILES that CDK
        declines stays as RDKit left it. A call that fails, and a structure CDK ran out of time or
        broke on, leave the structures invalid and give one warning; the job is never failed by
        it. Off with `CDK_FALLBACK=false`, and without chem."""
        accepted: dict[int, Chemistry] = {}
        if not (self.worker.cdk_fallback and self.services.chem_enabled):
            return accepted
        # One text per structure, used for the filter, the request, the molblock's reading and the
        # result: what ocsr wrote without the white space around it (RDKit strips it too).
        texts = {index: chem.smiles.strip() for index, chem in enumerate(chemistry)}
        asked = [
            index
            for index, chem in enumerate(chemistry)
            if not chem.valid and CHEM_SMILES.fullmatch(texts[index])
        ]
        for batch in itertools.batched(asked, self.worker.chem_batch_size):
            try:
                answers = await self.services.cdk([texts[index] for index in batch])
            except ServiceError as error:
                log.warning("job %s: %s", self.job.id, error)
                self._cdk_unavailable("CDK is unavailable at the moment.")
                if isinstance(error, ServiceTimedOut | ServiceUnreachable):
                    self.chem_hung = True  # a call for the names would wait as long again
                break
            for index, answer in zip(batch, answers, strict=True):
                if answer.molfile is None:
                    if answer.reason not in CDK_DECLINED:  # CDK did not get to decide
                        self._cdk_unsure()
                    continue
                async with self._analysis_slots:
                    try:
                        accepted[index] = await asyncio.to_thread(
                            from_cdk, texts[index], answer.molfile
                        )
                    except MolfileTooBig as error:
                        # CDK read it and laid it out; our molfile gate will not store it. A
                        # decline: the structure stays invalid, and chem did nothing wrong.
                        log.debug("job %s: structure %d: %s", self.job.id, index + 1, error)
                    except MolfileError as error:  # chem sent something that is no molblock
                        log.warning(
                            "job %s: structure %d: chem sent an unusable CDK molblock: %s",
                            self.job.id,
                            index + 1,
                            error,
                        )
                        self._cdk_unsure()
        return accepted

    def _cdk_unsure(self) -> None:
        self._cdk_unavailable("CDK could not check some structures that RDKit could not read.")

    def _cdk_unavailable(self, message: str) -> None:
        if not self.cdk_warned:
            self.cdk_warned = True
            self.warn(StageName.CHECK, "cdk_unavailable", message)

    async def _names(self, chemistry: list[Chemistry], incomplete: list[bool]) -> list[Name]:
        """IUPAC names from chem, one batch per `chem_batch_size` structures (a job has 20, so
        one request). A structure that is invalid, has a dummy atom (an R group) or is too long
        for chem is not asked about, nor is an incomplete one: a checked name would read as
        "this is the structure in the image", and the SMILES is a cut-off the model gave up on.
        Any failure, and a structure chem ran out of time on, leaves the names empty and gives
        one warning; the job is never failed by it."""
        names: list[Name] = [NO_NAME] * len(chemistry)
        if not self.services.chem_enabled:
            return names
        asked = [
            index
            for index, chem in enumerate(chemistry)
            if chem.valid and nameable(chem.smiles) and not incomplete[index]
        ]
        if asked and self.chem_hung:
            # The CDK call timed out or could not reach chem: the names would meet the same.
            self._names_unavailable("IUPAC names are unavailable at the moment.")
            return names
        for batch in itertools.batched(asked, self.worker.chem_batch_size):
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
            self.warn(StageName.CHECK, "names_unavailable", message)


# --- helpers -------------------------------------------------------------------------------------


def _whole(page: Page) -> tuple[int, int, int, int]:
    return (0, 0, page.width, page.height)


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


def _finalise(folder: Path, released: Released[CropRef]) -> list[Crop]:
    """Give the part files of a page that are inside the cap their final names, `crop-<n>.png`,
    and delete the rest."""
    crops: list[Crop] = []
    for index, ref in released.kept:
        path = folder / f"crop-{index}.png"
        if ref.written:
            os.replace(ref.part, path)
        else:  # no crop could be written: none is left over from an earlier run either
            path.unlink(missing_ok=True)
        crops.append(Crop(index, ref.page_index, ref.bbox, path))
    for ref in released.dropped:
        ref.part.unlink(missing_ok=True)
    return crops


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
