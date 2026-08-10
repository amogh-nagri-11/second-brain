from src.embeddings.provider import get_embedder
from src.storage.db import get_clusters_version, get_connection, load_clusters
from src.retrieval.search import search
from src.synthesis.answer import synthesize_answer

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

def get_answer(query_text: str) -> str:
    conn = get_connection()
    try:
        clusters = get_clusters(conn)
    finally:
        conn.close()

    if not clusters:
        return "I haven't ingested anything yet"

    query_embedding = get_embedder().embed(query_text)

    results = search(query_embedding, query_text, clusters)
    if not results:
        return "I don't have anything relating to that yet"

    top_cluster, _ = results[0]
    return synthesize_answer(query_text, top_cluster)
