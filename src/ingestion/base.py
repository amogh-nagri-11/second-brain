"""What a source is.

Everything sync knows about a source is here, so adding one means writing a
module and listing it -- not editing sync. A source says how to reach it, what it
returns, and the few things that differ between sources: how far the first sync
reaches back, whether a fetch is complete enough to infer deletions from, and
whatever else keeps its stored items current.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

# how far back to look the very first time a source is synced. Sources with
# cheap history (one search covers years) override it
INITIAL_LOOKBACK_DAYS = 90


@dataclass
class Source:
    # the sync cursor is kept under this, so changing it re-syncs from scratch
    name: str
    # set up at all? A source you haven't configured is skipped, not failed, so
    # someone with no GitHub token can still ask about their calendar
    configured: Callable[[], bool]
    # everything since a moment, as ActivityRecords
    fetch: Callable[[datetime], list]
    # the kinds of item it produces ("commit", "pr", "event"). Counting offers
    # these as filters, so a new source becomes countable by saying so here
    kinds: tuple[str, ...] = ()
    # does a fetch return *everything* in its window? Then anything stored in the
    # window that didn't come back has been deleted. Not true of GitHub, which
    # skips repos with no recent pushes
    complete_window: bool = False
    # stronger: a fetch returns everything there has ever been, whatever the
    # window -- true of a folder scan, and it means a note deleted years after it
    # was written still leaves the store
    complete_history: bool = False
    # anything else to keep stored items current, run after a successful fetch.
    # Takes (conn, log), returns how many items it changed
    after: Callable | None = None
    # how far back the very first sync of this source reaches
    first_lookback_days: int = INITIAL_LOOKBACK_DAYS
    # what to call it when something goes wrong, and in the README
    description: str = ""


@dataclass
class Registry:
    """The sources this app has. Ordered, since sync reports them in order."""

    sources: list[Source] = field(default_factory=list)

    def __iter__(self):
        return iter(self.sources)

    def __len__(self) -> int:
        return len(self.sources)

    def get(self, name: str) -> Source | None:
        return next((source for source in self.sources if source.name == name), None)

    def kinds(self) -> list[str]:
        seen = []
        for source in self.sources:
            seen.extend(kind for kind in source.kinds if kind not in seen)
        return seen
