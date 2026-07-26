import numpy as np 
from datetime import datetime 
from dateutil import parser as date_parser 

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float: 
    # np.linalg.norm(a) -> returs the totla geometric length of each vector using pythogoras theorem  
    # formula for cosine similarity = (a . b) / (||a||*||b||)
    return float(np.dot(a,b) / (np.linalg.norm(a) * np.linalg.norm(b))) 

def time_diff_hours(ts1: str, ts2: str) -> float: 
    t1 = date_parser.parse(ts1) 
    t2 = date_parser.parse(ts2) 
    return abs((t1-t2).total_seconds())/3600

def link_entities(
        records: list[dict], 
        simlarity_threshold: float = 0.5, 
        time_window_hours: float = 48.0,
) -> list[list[dict]]: 
    clusters: list[list[dict]] = [] 
    assigned = set()

    for i, record in enumerate(records): 
        if record["id"] in assigned: 
            continue 

        cluster = [record]
        assigned.add(record["id"]) 

        for j, other in enumerate(records): 
            if i==j or other["id"] in assigned: 
                continue 

            sim = cosine_similarity(record["embedding"], other["embedding"]) 
            time_gap = time_diff_hours(record["timestamp"], other["timestamp"]) 

            if sim>=simlarity_threshold and time_gap<=time_window_hours: 
                cluster.append([other]) 
                assigned.add(other["id"])

        clusters.append(cluster) 

    return clusters 


