from src.storage.db import get_connection, load_all_records
from src.entities.linking import link_entities
from src.embeddings.provider import EmbeddingProvider
from src.retrieval.search import search
from src.synthesis.answer import synthesize_answer

def main():
    conn = get_connection()
    records = load_all_records(conn)
    clusters = link_entities(records)

    embedder = EmbeddingProvider()

    query = input("Ask a question: ")
    query_embedding = embedder.embed(query)

    results = search(query_embedding, query, clusters)

    top_cluster, top_score = results[0]
    print(f"\nTop match (score: {top_score:.3f}):")
    for r in top_cluster:
        print(f"  [{r['source']}] {r['title']}")

    answer = synthesize_answer(query, top_cluster)
    print(f"\nAnswer: {answer}")

if __name__ == "__main__":
    main()