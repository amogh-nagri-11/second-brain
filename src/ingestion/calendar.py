from datetime import datetime 
from googleapiclient.discovery import build 
from src.config.google_auth import get_calendar_credentials 
from src.storage.types import ActivityRecord 

def fetch_recent_events(since: datetime) -> list[ActivityRecord]: 
    creds = get_calendar_credentials() 
    service = build("calendar", "v3", credentials=creds) 

    events_results = service.events().list(
        calendarId="primary", 
        timeMin=since.isoformat(), 
        singleEvents=True,
        orderBy="startTime" 
    ).execute() 

    events = events_results.get("items", [])
    records = []

    for event in events:
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        title = event.get("summary", "(no title)")
        body = event.get("description", "")

        records.append(ActivityRecord(
            id=f"calendar:{event['id']}",
            source="calendar",
            timestamp=start,
            title=title,
            body=body,
            url=event.get("htmlLink"),
            raw=event,
        ))

    return records