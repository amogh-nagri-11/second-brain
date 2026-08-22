from datetime import datetime, timedelta, timezone
from src.ingestion.github import fetch_recent_commits

def main():
    since = datetime.now(timezone.utc) - timedelta(days=90)

    activities = fetch_recent_commits(since)
    print(f"Found {len(activities)} commits")
    for a in activities:
        print(a.model_dump_json(indent=2))

if __name__ == "__main__":
    main()
