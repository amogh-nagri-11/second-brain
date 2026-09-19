from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from src.config.google_auth import get_calendar_credentials
from src.storage.db import event_fields
from src.storage.types import ActivityRecord

# Google expands recurring events one instance at a time when singleEvents=True, and
# open-ended recurrences (auto-generated birthdays, anniversaries) run forever. Without
# an upper bound a single birthday floods the store with decades of future rows, so we
# only ingest what has actually happened.
PAGE_SIZE = 250

# When the normal window turns up nothing, reach back this far to find whatever the most
# recent real activity was, so a quiet stretch doesn't leave the store empty.
FALLBACK_LOOKBACK_DAYS = 1825
FALLBACK_LIMIT = 50


def _list_events(service, time_min: datetime, time_max: datetime) -> list[dict]:
    events = []
    page_token = None
    while True:
        response = service.events().list(
            calendarId="primary",
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=PAGE_SIZE,
            pageToken=page_token,
        ).execute()

        events.extend(response.get("items", []))

        # a busy first sync easily exceeds one page; without this the tail is
        # silently dropped
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return events


def _to_record(event: dict) -> ActivityRecord:
    start = event.get("start", {})
    all_day = "dateTime" not in start

    return ActivityRecord(
        id=f"calendar:{event['id']}",
        source="calendar",
        kind="event",
        timestamp=start.get("dateTime") or start.get("date"),
        all_day=all_day,
        title=event.get("summary", "(no title)"),
        body=event.get("description", ""),
        url=event.get("htmlLink"),
        fields=event_fields(event),
        raw=event,
    )


def fetch_recent_events(since: datetime) -> list[ActivityRecord]:
    creds = get_calendar_credentials()
    service = build("calendar", "v3", credentials=creds)

    until = datetime.now(timezone.utc)
    events = _list_events(service, since, until)

    if not events:
        # nothing in the requested window -- fall back to the newest events from
        # whenever they happened, ordered ascending so the tail is the most recent
        older = _list_events(service, until - timedelta(days=FALLBACK_LOOKBACK_DAYS), since)
        events = older[-FALLBACK_LIMIT:]

    return [_to_record(event) for event in events]
