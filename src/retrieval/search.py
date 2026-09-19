import re
from datetime import datetime, timezone

import numpy as np 
from dateutil import parser as date_parser

from src.entities.linking import cosine_similarity 

# how fast a cluster's score decays with age: at one half-life old it contributes
# half the recency term. Long enough that a topical match from months ago still
# ranks, short enough that "latest" and "last" mean something.
RECENCY_HALF_LIFE_DAYS = 45

def cluster_embedding(cluster: list[dict]) -> np.ndarray: 
    embeddings = np.stack([r['embedding'] for r in cluster])
    return embeddings.mean(axis=0)

# words every question is made of. Left in, "what did I do on the parser" scores
# most of its overlap from "what", "I" and "on", which every record can match, and
# the one word that picks a record out -- "parser" -- is a fifth of the score.
STOPWORDS = frozenset("""
    a about all am an and any are as at be been by can could did do does done for
    from get got had has have how i in is it its last latest me my of on or our so
    than that the their them then there these this those to up was we were what
    when where which who why will with work worked working you your
""".split())

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")


def _words(text: str) -> set[str]:
    # a regex rather than str.split, so "parser?" and "(parser)" both match "parser"
    return set(_WORD_RE.findall(text.lower())) - STOPWORDS


def keyword_overlap_score(query: str, cluster: list[dict]) -> float:
    query_words = _words(query)
    if not query_words:
        return 0.0
    cluster_words = _words(" ".join(f"{r['title']} {r['body']}" for r in cluster))
    return len(query_words & cluster_words) / len(query_words)

def _newest(cluster: list[dict]) -> datetime:
    """All-day calendar events carry a bare date and commits a tz-aware timestamp,
    so both get normalised before they are compared."""
    moments = []
    for record in cluster:
        moment = date_parser.parse(record["timestamp"])
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moments.append(moment)
    return max(moments)


def recency_score(cluster: list[dict], now: datetime) -> float:
    age_days = max((now - _newest(cluster)).total_seconds() / 86400, 0.0)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def search(
    query_embedding: np.ndarray, 
    query_text: str, 
    clusters: list[list[dict]], 
    top_k: int = 3,
    now: datetime | None = None,
) -> list[tuple[list[dict], float]]:
    now = now or datetime.now(timezone.utc)

    scored = [] 
    for cluster in clusters: 
        cluster_vec = cluster_embedding(cluster) 
        semantic_vec = cosine_similarity(query_embedding, cluster_vec) 
        keyword_score = keyword_overlap_score(query_text, cluster) 
        # without this nothing in the ranking knows what "latest" means, and a
        # question about the newest commit can be answered from months-old ones
        # that happened to embed slightly closer
        recency = recency_score(cluster, now)

        combined_score = (0.7 * semantic_vec) + (0.15 * keyword_score) + (0.15 * recency)
        scored.append((cluster, combined_score)) 

    scored.sort(key=lambda x: x[1], reverse=True) 
    return scored[:top_k]


    

def score_records(
    query_embedding: np.ndarray,
    query_text: str,
    records: list[dict],
    now: datetime | None = None,
) -> list[tuple[dict, float]]:
    """Rank individual records, not clusters.

    Clustering groups things that happened together, which is what makes a topical
    answer coherent -- but it also means a question whose matches are scattered
    across many small clusters only ever sees the few clusters that rank, and
    answers "how many" from a fraction of them. Scoring records directly is what
    makes a count come out right; the caller still folds in cluster context.
    """
    now = now or datetime.now(timezone.utc)

    scored = [
        (
            record,
            (0.7 * cosine_similarity(query_embedding, record["embedding"]))
            + (0.15 * keyword_overlap_score(query_text, [record]))
            + (0.15 * recency_score([record], now)),
        )
        for record in records
    ]

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored
