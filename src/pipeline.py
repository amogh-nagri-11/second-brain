import re
from datetime import datetime, timezone

from dateutil import parser as date_parser

from openai import APIConnectionError, APIStatusError, RateLimitError

from src.config.env import MissingCredential
from src.embeddings.provider import get_embedder
from src.storage.db import get_clusters_version, get_connection, keyword_scores, load_clusters
from src.retrieval.search import query_words, score_records, search
from src.synthesis.answer import Answer, synthesize_answer
from src.synthesis.rewrite import standalone_question

# how many of the ranked clusters feed the answer. Anything that spans separate
# occasions -- "how many times", "list every" -- lands in several singleton clusters
# rather than one, so answering from the single best cluster silently truncates it.
# Keep this well above the number of clusters a question plausibly spans: singleton
# clusters mean k clusters is only k records, and MAX_CONTEXT_RECORDS is the real budget.
TOP_K_CLUSTERS = 15
# ceiling on records handed to the model, so a big cluster can't blow up the prompt
# -- what is sent is what is paid for, and a longer prompt is a slower answer.
# Counting needs breadth, so most of this budget is spent on one-line entries --
# only the best-ranked few carry their full text.
MAX_CONTEXT_RECORDS = 60
DETAILED_RECORDS = 18

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


def _context_records(results, ranked: list[tuple[dict, float]]) -> list[dict]:
    """Build the context from individually ranked records, then top up with the best
    cluster.

    Records first, because a question like "how many times" has its matches spread
    over many small clusters and cluster ranking only ever surfaces a few of them.
    The winning cluster is added afterwards so a topical question still gets the
    things that happened alongside its best match.
    """
    records = []
    seen = set()

    for record, _score in ranked[:MAX_CONTEXT_RECORDS]:
        seen.add(record["id"])
        records.append(record)

    if results:
        for record in results[0][0]:
            if record["id"] in seen or len(records) >= MAX_CONTEXT_RECORDS:
                continue
            seen.add(record["id"])
            records.append(record)

    # copies, because these dicts live in the cluster cache and the detail flag is
    # per-question -- mutating them would leak into the next one
    records = [dict(record, detailed=rank < DETAILED_RECORDS) for rank, record in enumerate(records)]

    records.sort(key=_recorded_at, reverse=True)
    return records


def retrieve(
    conn, query_text: str, clusters: list[list[dict]], now: datetime | None = None
) -> tuple[list[dict], list[dict]]:
    """(the records the model is shown, every record ranked best first).

    The one path from a question to its context -- the app answers from it and the
    retrieval check scores it, so the check measures what the app actually does.
    """
    query_embedding = get_embedder().embed(query_text)
    keywords = keyword_scores(conn, query_words(query_text))
    results = search(query_embedding, query_text, clusters, top_k=TOP_K_CLUSTERS, now=now, keywords=keywords)
    ranked = score_records(
        query_embedding, query_text, [r for c in clusters for r in c], now=now, keywords=keywords
    )
    return _context_records(results, ranked), [record for record, _score in ranked]


def _retry_hint(error) -> str:
    """Some providers say when to come back, in the message text; pass that on when
    they do. Most don't, so the general reply is the common path."""
    match = re.search(r"try again in ([\w.]+)", str(error))
    return f"Try again in {match.group(1)}." if match else "Try again in a minute."


# what a refusal from the provider actually means, in words worth hearing
_REFUSALS = {
    401: "my API key isn't being accepted",
    402: "the account is out of credit",
    403: "this account isn't allowed to use that model",
    404: "that model doesn't exist at this provider",
}


def _refusal(error: "APIStatusError") -> str:
    reason = _REFUSALS.get(error.status_code, f"it returned {error.status_code}")
    return f"The model provider refused: {reason}. Check: python -m src.config.settings"


def get_answer(query_text: str, turns=None) -> Answer:
    """Answer a question, optionally as the next turn of a conversation.

    `turns` are the turns before this one, empty for a question asked out of the
    blue. Retrieval runs on the rewritten question rather than on the previous
    turn's records: holding those in front of the model made it answer "how many"
    by counting them again, which is the thing the count exists to stop.
    """
    conn = get_connection()
    try:
        clusters = get_clusters(conn)
        if not clusters:
            return Answer(spoken="I haven't ingested anything yet", written="")

        # retrieval searches for what the question means, which for a follow-up is
        # not what it says
        search_text = standalone_question(query_text, turns or [])
        context, _ranked = retrieve(conn, search_text, clusters)

        if not context:
            return Answer(spoken="I don't have anything relating to that yet", written="")

        try:
            # the connection stays open through synthesis: counting questions are
            # answered from the store itself, not from the records above
            return synthesize_answer(
                query_text, context, conn=conn, turns=turns or [], search_text=search_text
            )
        except MissingCredential as error:
            return Answer(
                spoken="I need an API key before I can answer",
                written=f"{error}\n\nOr point it somewhere else:"
                        " python -m src.config.settings llm_base_url <url>",
            )
        except RateLimitError as error:
            # whoever is actually serving the model can still throttle or run out
            # of credit, and this is the one place that turns a 500 into something
            # worth hearing
            return Answer(
                spoken=f"The model provider turned me down -- rate limit or credit. {_retry_hint(error)}",
                written=f"Rate limited by the model provider.\n\n{error}",
            )
        except APIStatusError as error:
            # a refusal, not a rate limit: a key that isn't accepted, no credit
            # left, a model this account may not use. Every one of these used to
            # reach the window as a bare 500
            return Answer(spoken=_refusal(error), written=f"{_refusal(error)}\n\n{error}")
        except APIConnectionError as error:
            return Answer(
                spoken="I couldn't reach the model provider -- check the network.",
                written=f"Couldn't reach the model provider.\n\n{error}",
            )
    finally:
        conn.close()
