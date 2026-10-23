"""The two rules the overlapped pipeline rests on; no I/O, so they are tested alone.

* `PrefixNumbering`: which structure of the job a crop is, and whether it is kept, decided as soon
  as it can be known and never differently from the sequential pipeline.
* `StageTracker`: the state of the four pipeline stages while several of them run at once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models import Stage, StageName, StageState

PIPELINE_STAGES = (StageName.RENDER, StageName.SEGMENT, StageName.RECOGNISE, StageName.CHECK)


@dataclass(frozen=True)
class Released[T]:
    """One page whose crops now have their places. `kept` pairs each crop that is inside the cap
    with its result index; `dropped` are the crops of the page that are beyond it."""

    position: int
    kept: list[tuple[int, T]]
    dropped: list[T]


class PrefixNumbering[T]:
    """Numbers the structures of a job in document order, as pages are segmented in any order.

    The result index of a crop is the number of structures on all earlier pages plus its place
    on its own page. It is therefore known only when every earlier page is settled (segmented,
    failed, or not rendered): the PREFIX RULE. `settle` holds a page back until its predecessors
    are in and then releases it together with any successors that were waiting, in page order.
    Releasing a page on arrival would number pages in the order seg answered, and a different
    result (another page's structure inside the cap) whenever seg does not answer in order.

    The cap is applied to the index: a crop whose index is `cap` or more is dropped. The count of
    structures on a page (`found`) can exceed the crops kept for it (`items`, at most `cap` of
    them); the count is what moves the numbering on.
    """

    def __init__(self, positions: int, cap: int) -> None:
        self._positions = positions
        self._cap = cap
        self._waiting: dict[int, tuple[int, Sequence[T]]] = {}
        self._next = 0  # the first position that has not been released
        self.total = 0  # structures on the pages released so far
        self.kept = 0  # of those, the ones inside the cap

    @property
    def complete(self) -> bool:
        """Every position is settled and released: `total` is the job's structure count."""
        return self._next == self._positions

    def settle(self, position: int, found: int, items: Sequence[T]) -> list[Released[T]]:
        """Page `position` is done: `found` structures, of which `items` (the first ones, in
        order) are kept for numbering. A page that failed settles with `found=0`."""
        if (
            not 0 <= position < self._positions
            or position < self._next
            or position in self._waiting
        ):
            raise ValueError(f"page position {position} cannot be settled")
        self._waiting[position] = (found, items)
        released: list[Released[T]] = []
        while self._next in self._waiting:
            found, items = self._waiting.pop(self._next)
            kept = [
                (self.total + n, item) for n, item in enumerate(items) if self.total + n < self._cap
            ]
            dropped = [item for n, item in enumerate(items) if self.total + n >= self._cap]
            released.append(Released(self._next, kept, dropped))
            self.total += found
            self.kept += len(kept)
            self._next += 1
        return released


class StageTracker:
    """The state of render, segment, recognise and check while they overlap.

    A stage is told when its first unit of work starts (`start`) and when its last one ends
    (`finish`). Its state follows from that, and from the stages before it:

    * pending: nothing of it has started;
    * active: it has started, or it has finished but a stage before it has not;
    * done: it has finished AND every stage before it is done.

    So a stage never shows done before an earlier one does, whatever order the work ends in, and
    a stage without any work (no structures, so nothing to recognise) goes straight to done.
    """

    def __init__(self) -> None:
        self._started: set[StageName] = set()
        self._finished: set[StageName] = set()

    def start(self, name: StageName) -> None:
        self._started.add(name)

    def finish(self, name: StageName) -> None:
        self._finished.add(name)

    def states(self) -> dict[StageName, StageState]:
        states: dict[StageName, StageState] = {}
        earlier_done = True
        for name in PIPELINE_STAGES:
            if name in self._finished and earlier_done:
                state = StageState.DONE
            elif name in self._started or name in self._finished:
                state = StageState.ACTIVE
            else:
                state = StageState.PENDING
            earlier_done = earlier_done and state is StageState.DONE
            states[name] = state
        return states

    def apply(self, stages: list[Stage]) -> bool:
        """Write the states into the record's stage list; True when any of them changed."""
        wanted = self.states()
        changed = False
        for stage in stages:
            state = wanted.get(stage.name)
            if state is not None and stage.state is not state:
                stage.state = state
                changed = True
        return changed
