from datetime import datetime, timedelta, timezone
from src.ingestion.calendar import fetch_recent_events

def main():
    since = datetime.now(timezone.utc) - timedelta(days=30)
    activities = fetch_recent_events(since)
    print(f"Found {len(activities)} events")
    for a in activities:
        print(a.model_dump_json(indent=2))

if __name__ == "__main__":
    main()