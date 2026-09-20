"""The sources this app has.

One list, in sync order. A new source is a module in this package that ends with
a `SOURCE = Source(...)`, plus its line here -- sync, counting and the README
read it from this list rather than knowing the sources themselves.
"""

from src.ingestion.base import Registry
from src.ingestion.calendar import SOURCE as CALENDAR
from src.ingestion.github import SOURCE as GITHUB_COMMITS
from src.ingestion.github_prs import SOURCE as GITHUB_PRS
from src.ingestion.notes import SOURCE as NOTES

REGISTRY = Registry([GITHUB_COMMITS, GITHUB_PRS, CALENDAR, NOTES])


def sources() -> Registry:
    return REGISTRY
