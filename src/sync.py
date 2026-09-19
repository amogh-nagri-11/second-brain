"""Incremental ingestion.

Each source records how far it has been synced, so a run only pulls what is new
instead of re-fetching and re-embedding the whole history. Clusters are rebuilt
here (once per sync) rather than on every question.
"""

from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser

from src.config.env import get_secret
from src.config.paths import google_client_path, google_token_path
from src.embeddings.provider import get_embedder
from src.entities.linking import link_entities
from src.ingestion.calendar import fetch_recent_events
from src.ingestion.github import fetch_recent_commits
from src.storage.db import (
    existing_fingerprints,
    get_connection,
    get_last_synced_at,
    load_all_records,
    save_clusters,
    save_record,
    set_last_synced_at,
)

# how far back to look the very first time a source is synced
INITIAL_LOOKBACK_DAYS = 90
# re-scan a window before the last sync rather than starting exactly where we left off.
# GitHub filters commits by commit date, not push date, so work committed locally and
# pushed days later lands *behind* the cursor and would otherwise be missed forever.
# A week covers realistic push lag; calendar events also get edited after creation.
# Costs nothing either way since unchanged records are skipped before embedding.
OVERLAP_HOURS = 24 * 7


def _since_for(conn, source: str) -> datetime:
    last = get_last_synced_at(conn, source)
    if last is None:
        return datetime.now(timezone.utc) - timedelta(days=INITIAL_LOOKBACK_DAYS)
    return date_parser.parse(last) - timedelta(hours=OVERLAP_HOURS)


# name -> (is it set up?, fetch everything since a moment)
SOURCES = {
    "github": (lambda: bool(get_secret("GITHUB_TOKEN")), fetch_recent_commits),
    "calendar": (
        lambda: google_token_path().exists() or google_client_path().exists(),
        fetch_recent_events,
    ),
}


def _fetch(conn, source: str) -> list:
    _configured, fetch = SOURCES[source]
    return fetch(_since_for(conn, source))


def run_sync(conn=None, log=print) -> dict:
    """Pull new records from every source, embed only what changed, rebuild clusters.

    Returns counts so callers (CLI, menubar) can report what happened.
    """
    owns_connection = conn is None
    conn = conn or get_connection()

    try:
        known = existing_fingerprints(conn)
        embedder = get_embedder()

        added = 0
        updated = 0
        skipped = 0
        failed: list[str] = []
        not_configured: list[str] = []

        for source, (configured, _fetch_since) in SOURCES.items():
            # a source you haven't set up isn't a failure -- skip it quietly and
            # sync the rest
            if not configured():
                not_configured.append(source)
                log(f"[sync] {source}: not set up, skipped")
                continue

            started_at = datetime.now(timezone.utc)

            try:
                records = _fetch(conn, source)
            except Exception as error:
                # one source being down shouldn't block the other, and we deliberately
                # don't advance its cursor so the next run retries the same window
                failed.append(source)
                log(f"[sync] {source} failed: {error}")
                continue

            for record in records:
                fingerprint = (record.title, record.body)
                if known.get(record.id) == fingerprint:
                    skipped += 1
                    continue

                embedding = embedder.embed(f"{record.title}\n{record.body}")
                save_record(conn, record, embedding)

                if record.id in known:
                    updated += 1
                else:
                    added += 1
                known[record.id] = fingerprint

            set_last_synced_at(conn, source, started_at.isoformat())
            log(f"[sync] {source}: {len(records)} fetched")

        changed = added + updated
        if changed:
            clusters = link_entities(load_all_records(conn))
            save_clusters(conn, clusters)
            log(f"[sync] rebuilt {len(clusters)} clusters")

        log(f"[sync] +{added} new, {updated} updated, {skipped} unchanged")
        return {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "not_configured": not_configured,
            "changed": bool(changed),
        }
    finally:
        if owns_connection:
            conn.close()


if __name__ == "__main__":
    run_sync()
