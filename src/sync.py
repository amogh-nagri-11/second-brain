"""Incremental ingestion.

Each source records how far it has been synced, so a run only pulls what is new
instead of re-fetching and re-embedding the whole history. Clusters are rebuilt
here (once per sync) rather than on every question.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from dateutil import parser as date_parser

from src.config.env import get_secret
from src.config.paths import google_client_path, google_token_path
from src.embeddings.chunking import CHUNKING_VERSION, chunk_texts
from src.embeddings.provider import MODEL_ID, get_embedder
from src.entities.linking import link_entities
from src.ingestion.calendar import fetch_recent_events
from src.ingestion.github import as_merged, fetch_recent_commits, merged_commits
from src.ingestion.github_prs import fetch_recent_prs
from src.storage.db import (
    existing_fingerprints,
    get_connection,
    get_last_synced_at,
    get_meta,
    set_meta,
    item_texts,
    live_ids_between,
    load_all_records,
    load_item,
    mark_deleted,
    record_fingerprint,
    replace_chunks,
    save_clusters,
    save_item,
    set_last_synced_at,
    unmerged_commits,
)

# how far back to look the very first time a source is synced
INITIAL_LOOKBACK_DAYS = 90
# pull requests are worth reaching much further back for: the whole history costs
# one search either way, and "how many have I merged" is wrong without it
PR_LOOKBACK_DAYS = 365 * 5
# re-scan a window before the last sync rather than starting exactly where we left off.
# GitHub filters commits by commit date, not push date, so work committed locally and
# pushed days later lands *behind* the cursor and would otherwise be missed forever.
# A week covers realistic push lag; calendar events also get edited after creation.
# Costs nothing either way since unchanged records are skipped before embedding.
OVERLAP_HOURS = 24 * 7
# when reconciling deletions, stay this far inside the fetched window: an event's
# stored time and the time the source filters on can sit either side of an edge
# (all-day entries are dates in your own time zone), and a miss there would wrongly
# delete. The week of overlap means nothing is left unchecked.
RECONCILE_MARGIN = timedelta(days=1)
# how far back, and how many, unmerged commits are re-checked each sync
MERGE_CHECK_DAYS = 180
MERGE_CHECK_LIMIT = 50


def _since_for(conn, source: str, first_lookback_days: int = INITIAL_LOOKBACK_DAYS) -> datetime:
    last = get_last_synced_at(conn, source)
    if last is None:
        return datetime.now(timezone.utc) - timedelta(days=first_lookback_days)
    return date_parser.parse(last) - timedelta(hours=OVERLAP_HOURS)


@dataclass
class Source:
    """What sync needs from a source."""

    # set up at all? a source that isn't is skipped, not failed
    configured: Callable[[], bool]
    # everything since a moment, as ActivityRecords
    fetch: Callable[[datetime], list]
    # does a fetch return *everything* in its window? Then anything stored in the
    # window that didn't come back has been deleted. Not true of GitHub, which
    # skips repos with no recent pushes.
    complete_window: bool = False
    # anything else to keep stored items current, run after a successful fetch;
    # returns how many items it changed
    after: Callable | None = None
    # how far back the very first sync of this source reaches
    first_lookback_days: int = INITIAL_LOOKBACK_DAYS


def _update_merged(conn, log) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=MERGE_CHECK_DAYS)).isoformat()
    candidates = unmerged_commits(conn, since, MERGE_CHECK_LIMIT)
    if not candidates:
        return 0
    merged = merged_commits(candidates)
    for item_id in merged:
        record = as_merged(load_item(conn, item_id))
        save_item(conn, record, embed_chunks(record.title, record.body))
    if merged:
        log(f"[sync] github: {len(merged)} branch commits have since merged")
    return len(merged)


SOURCES = {
    "github": Source(
        configured=lambda: bool(get_secret("GITHUB_TOKEN")),
        fetch=fetch_recent_commits,
        after=_update_merged,
    ),
    # its own source, not part of "github", so one search failing can't stop
    # commits from being ingested and each keeps its own cursor
    "github_prs": Source(
        configured=lambda: bool(get_secret("GITHUB_TOKEN")),
        fetch=fetch_recent_prs,
        first_lookback_days=PR_LOOKBACK_DAYS,
    ),
    "calendar": Source(
        configured=lambda: google_token_path().exists() or google_client_path().exists(),
        fetch=fetch_recent_events,
        complete_window=True,
    ),
}


def _reconcile(conn, source: str, since: datetime, until: datetime, fetched: set[str]) -> int:
    start, end = since + RECONCILE_MARGIN, until - timedelta(hours=1)
    if start >= end:
        return 0
    gone = live_ids_between(conn, source, start.isoformat(), end.isoformat()) - fetched
    mark_deleted(conn, sorted(gone))
    return len(gone)


def _embedding_version() -> str:
    return f"{MODEL_ID}|{CHUNKING_VERSION}"


def embed_chunks(title: str, body: str) -> list[tuple[str, list[float]]]:
    texts = chunk_texts(title, body)
    return list(zip(texts, get_embedder().embed_batch(texts)))


def reembed_if_stale(conn, log=print, force: bool = False) -> bool:
    """Vectors from two models (or two ways of chunking) can't be compared with
    each other, so a change to either means every item is chunked and embedded
    again, from the text already stored -- nothing is fetched."""
    if not force and get_meta(conn, "embedding_model") == _embedding_version():
        return False

    items = item_texts(conn)
    texts = [chunk_texts(title, body) for _id, title, body in items]
    vectors = iter(get_embedder().embed_batch([t for pieces in texts for t in pieces]))

    with conn:
        for (item_id, _title, _body), pieces in zip(items, texts):
            replace_chunks(conn, item_id, [(text, next(vectors)) for text in pieces])
    set_meta(conn, "embedding_model", _embedding_version())
    log(f"[sync] re-embedded {len(items)} items with {_embedding_version()}")
    return bool(items)


def run_sync(conn=None, log=print) -> dict:
    """Pull new records from every source, embed only what changed, rebuild clusters.

    Returns counts so callers (CLI, menubar) can report what happened.
    """
    owns_connection = conn is None
    conn = conn or get_connection()

    try:
        reembedded = reembed_if_stale(conn, log)
        known = existing_fingerprints(conn)

        added = 0
        updated = 0
        skipped = 0
        deleted = 0
        failed: list[str] = []
        not_configured: list[str] = []

        for name, source in SOURCES.items():
            # a source you haven't set up isn't a failure -- skip it quietly and
            # sync the rest
            if not source.configured():
                not_configured.append(name)
                log(f"[sync] {name}: not set up, skipped")
                continue

            started_at = datetime.now(timezone.utc)
            since = _since_for(conn, name, source.first_lookback_days)

            try:
                records = source.fetch(since)
            except Exception as error:
                # one source being down shouldn't block the other, and we deliberately
                # don't advance its cursor so the next run retries the same window
                failed.append(name)
                log(f"[sync] {name} failed: {error}")
                continue

            for record in records:
                fingerprint = record_fingerprint(record)
                if known.get(record.id) == fingerprint:
                    skipped += 1
                    continue

                save_item(conn, record, embed_chunks(record.title, record.body))

                if record.id in known:
                    updated += 1
                else:
                    added += 1
                known[record.id] = fingerprint

            if source.complete_window:
                gone = _reconcile(conn, name, since, started_at, {r.id for r in records})
                if gone:
                    log(f"[sync] {name}: {gone} no longer there, marked deleted")
                deleted += gone

            if source.after is not None:
                try:
                    updated += source.after(conn, log)
                except Exception as error:
                    log(f"[sync] {name}: follow-up failed: {error}")

            set_last_synced_at(conn, name, started_at.isoformat())
            log(f"[sync] {name}: {len(records)} fetched")

        changed = added + updated + deleted + int(reembedded)
        if changed:
            clusters = link_entities(load_all_records(conn))
            save_clusters(conn, clusters)
            log(f"[sync] rebuilt {len(clusters)} clusters")

        log(f"[sync] +{added} new, {updated} updated, {deleted} deleted, {skipped} unchanged")
        return {
            "added": added,
            "updated": updated,
            "deleted": deleted,
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
