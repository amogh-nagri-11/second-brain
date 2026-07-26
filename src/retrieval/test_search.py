from src.storage.db import get_connection, load_all_records
from src.entities.linking import link_entities
from src.embeddings.provider import EmbeddingsProvider
from src.retrieval.search import search

def main():
    conn = get_connection()
    records = load_all_records(conn)
    clusters = link_entities(records)

    embedder = EmbeddingsProvider()

    query = input("Ask a question: ")
    query_embedding = embedder.embed(query)

    results = search(query_embedding, query, clusters)

    print(f"\nTop {len(results)} matching entities:\n")
    for cluster, score in results:
        print(f"--- Score: {score:.3f} ---")
        for r in cluster:
            print(f"  [{r['source']}] {r['title']}  ({r['timestamp']})")
        print()

if __name__ == "__main__":
    main()