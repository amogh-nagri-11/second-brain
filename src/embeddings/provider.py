from sentence_transformers import SentenceTransformer 
import numpy as np 

class EmbeddingProvider: 
    def __init__(self, model_name: str="all-MiniLM-L6-v2"): 
        self.model = SentenceTransformer(model_name) 

    def embed(self, text: str) -> list[float]: 
        vector = self.model.encode(text, convert_to_numpy=True) 
        return vector.tolist() 

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, convert_to_numpy=True) 
        return vectors.tolist() 

     
