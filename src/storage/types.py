from typing import Any, Literal

from pydantic import BaseModel

ActivitySource = Literal['github', 'calendar']


class ActivityRecord(BaseModel):
    """One thing that happened, as a source hands it over.

    `fields` holds what a question can be filtered on, and differs per kind:
    repo / branch / merged for a commit, attendees / location for an event. Adding
    a source means new fields, not new columns.
    """

    id: str
    source: ActivitySource
    # what it is, independent of where it came from: "commit", "event", ...
    kind: str
    timestamp: str
    # an all-day calendar entry has a date and no time
    all_day: bool = False
    title: str
    body: str
    url: str | None = None
    fields: dict[str, Any] = {}
    raw: dict[str, Any] = {}
