import numpy as np
from dateutil import parser as date_parser

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    # np.linalg.norm(a) -> returs the totla geometric length of each vector using pythogoras theorem
    # formula for cosine similarity = (a . b) / (||a||*||b||)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)

def time_diff_hours(ts1: str, ts2: str) -> float:
    return abs(_epoch_hours(ts1) - _epoch_hours(ts2))

def _epoch_hours(ts: str) -> float:
    # calendar all-day events come back as bare dates (naive), commits as ISO with
    # offsets (aware) -- going through .timestamp() keeps the two comparable
    return date_parser.parse(ts).timestamp() / 3600.0

def _unit_embeddings(records: list[dict]) -> np.ndarray:
    matrix = np.stack([np.asarray(r["embedding"], dtype=np.float32) for r in records])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms

def link_entities(
        records: list[dict],
        similarity_threshold: float = 0.2,
        time_window_hours: float = 48.0,
) -> list[list[dict]]:
    if not records:
        return []

    # pairwise similarity in one matmul instead of a python loop per pair
    unit = _unit_embeddings(records)
    similarity = unit @ unit.T

    hours = np.array([_epoch_hours(r["timestamp"]) for r in records])
    time_gap = np.abs(hours[:, None] - hours[None, :])

    eligible = (similarity >= similarity_threshold) & (time_gap <= time_window_hours)
    np.fill_diagonal(eligible, False)

    clusters: list[list[dict]] = []
    assigned = np.zeros(len(records), dtype=bool)

    for i in range(len(records)):
        if assigned[i]:
            continue

        assigned[i] = True
        members = np.flatnonzero(eligible[i] & ~assigned)
        assigned[members] = True

        clusters.append([records[i]] + [records[j] for j in members])

    return clusters
