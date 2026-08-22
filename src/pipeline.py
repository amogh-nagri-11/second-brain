from datetime import datetime, timezone

from dateutil import parser as date_parser

from src.embeddings.provider import get_embedder
from src.storage.db import get_clusters_version, get_connection, load_clusters
from src.retrieval.search import search
from src.synthesis.answer import Answer, synthesize_answer

# how many of the ranked clusters feed the answer. Anything that spans separate
# occasions -- "how many times", "list every" -- lands in several singleton clusters
# rather than one, so answering from the single best cluster silently truncates it.
# Keep this well above the number of clusters a question plausibly spans: singleton
# clusters mean k clusters is only k records, and MAX_CONTEXT_RECORDS is the real budget.
TOP_K_CLUSTERS = 15
# ceiling on records handed to the model, so a big cluster can't blow up the prompt
# (and, on the free Groq tier, can't trip the 8k tokens-per-minute limit)
MAX_CONTEXT_RECORDS = 25

# clusters only change when a sync runs, so keep them in memory and re-read
# only when the stored version counter moves
_cluster_cache: tuple[int, list[list[dict]]] | None = None

def get_clusters(conn) -> list[list[dict]]:
    global _cluster_cache

    version = get_clusters_version(conn)
    if _cluster_cache is None or _cluster_cache[0] != version:
        _cluster_cache = (version, load_clusters(conn))

    return _cluster_cache[1]

def invalidate_cluster_cache():
    global _cluster_cache
    _cluster_cache = None

def _recorded_at(record: dict) -> datetime:
    """All-day calendar events carry a bare date and github commits a tz-aware
    timestamp; normalise so the two can be ordered against each other."""
    moment = date_parser.parse(record["timestamp"])
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _context_records(results) -> list[dict]:
    """Flatten the ranked clusters into one newest-first list, keeping rank order for
    the dedupe so a record is attributed to the best-scoring cluster that held it."""
    records = []
    seen = set()

    for cluster, _ in results:
        for record in cluster:
            if record["id"] in seen:
                continue
            seen.add(record["id"])
            records.append(record)

    # trim in rank order -- trimming after the date sort would drop records for being
    # old rather than for being irrelevant, losing the very ones the query matched
    records = records[:MAX_CONTEXT_RECORDS]
    records.sort(key=_recorded_at, reverse=True)
    return records


def get_answer(query_text: str) -> Answer:
    conn = get_connection()
    try:
        clusters = get_clusters(conn)
    finally:
        conn.close()

    if not clusters:
        return Answer(spoken="I haven't ingested anything yet", written="")

    query_embedding = get_embedder().embed(query_text)

    results = search(query_embedding, query_text, clusters, top_k=TOP_K_CLUSTERS)
    if not results:
        return Answer(spoken="I don't have anything relating to that yet", written="")

    return synthesize_answer(query_text, _context_records(results))
