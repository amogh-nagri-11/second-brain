from src.embeddings.provider import EmbeddingProvider 
from src.storage.db import get_connection, load_all_records 
from src.entities.linking import link_entities
from src.retrieval.search import search 
from src.synthesis.answer import synthesize_answer

_embedder = None 

def get_embedder(): 
    global _embedder 
    if _embedder is None: 
        _embedder = EmbeddingProvider()  
    return _embedder 

def get_answer(query_text: str) -> str: 
    conn = get_connection() 
    records = load_all_records(conn) 
    clusters = link_entities(records) 

    embedder = get_embedder() 
    query_embedding = embedder.embed(query_text) 

    results = search(query_embedding, query_text, clusters) 
    if not results: 
        return "I don't have anything relating to that yet"

    top_cluster, _ = results[0] 
    return synthesize_answer(query_text, top_cluster) 
