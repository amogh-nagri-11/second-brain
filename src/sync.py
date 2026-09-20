"""Incremental ingestion.

Each source records how far it has been synced, so a run only pulls what is new
instead of re-fetching and re-embedding the whole history. Clusters are rebuilt
here (once per sync) rather than on every question.
"""

from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser

from src.embeddings.chunking import CHUNKING_VERSION, chunk_texts
from src.embeddings.provider import MODEL_ID, get_embedder
from src.entities.linking import link_entities
from src.ingestion.base import INITIAL_LOOKBACK_DAYS
from src.ingestion.registry import sources
from src.storage.db import (
    existing_fingerprints,
    get_connection,
    get_last_synced_at,
    get_meta,
    set_meta,
    item_texts,
    live_ids_between,
    load_all_records,
    mark_deleted,
    record_fingerprint,
    replace_chunks,
    save_clusters,
    save_item,
    set_last_synced_at,
)

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


def _since_for(conn, source: str, first_lookback_days: int = INITIAL_LOOKBACK_DAYS) -> datetime:
    last = get_last_synced_at(conn, source)
    if last is None:
        return datetime.now(timezone.utc) - timedelta(days=first_lookback_days)
    return date_parser.parse(last) - timedelta(hours=OVERLAP_HOURS)


def _reconcile(conn, source: str, since: datetime, until: datetime, fetched: set[str],
               all_history: bool = False) -> int:
    if all_history:
        # the fetch covered everything, so anything stored and not returned is gone,
        # however old or however far in the future it is dated
        start, end = datetime.min.replace(tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc)
    else:
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

        for source in sources():
            name = source.name
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

            if source.complete_window or source.complete_history:
                gone = _reconcile(conn, name, since, started_at, {r.id for r in records},
                                  all_history=source.complete_history)
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
