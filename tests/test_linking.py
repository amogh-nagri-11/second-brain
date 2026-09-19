import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from src.entities.linking import _epoch_hours, _unit_embeddings, link_entities


def all_pairs(records, similarity_threshold=0.2, time_window_hours=48.0):
    """The previous implementation: every pair compared, O(n^2) time and memory."""
    unit = _unit_embeddings(records)
    similarity = unit @ unit.T
    hours = np.array([_epoch_hours(r["timestamp"]) for r in records])
    eligible = (similarity >= similarity_threshold) & (np.abs(hours[:, None] - hours[None, :]) <= time_window_hours)
    np.fill_diagonal(eligible, False)
    clusters, assigned = [], np.zeros(len(records), dtype=bool)
    for i in range(len(records)):
        if assigned[i]:
            continue
        assigned[i] = True
        members = np.flatnonzero(eligible[i] & ~assigned)
        assigned[members] = True
        clusters.append([records[i]] + [records[j] for j in members])
    return clusters


def random_records(n, seed):
    rng = np.random.default_rng(seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # a few topics, so similar records exist, spread over ~60 days
    topics = rng.normal(size=(5, 16))
    records = []
    for i in range(n):
        vector = topics[rng.integers(5)] + rng.normal(scale=0.8, size=16)
        moment = start + timedelta(hours=float(rng.uniform(0, 24 * 60)))
        timestamp = moment.date().isoformat() if i % 7 == 0 else moment.isoformat()
        records.append({"id": str(i), "timestamp": timestamp, "embedding": vector.astype(np.float32)})
    return records


def ids(clusters):
    return [[r["id"] for r in cluster] for cluster in clusters]


class WindowedLinkingTests(unittest.TestCase):
    def test_matches_comparing_every_pair(self):
        for seed in range(5):
            records = random_records(400, seed)
            self.assertEqual(ids(link_entities(records)), ids(all_pairs(records)), f"seed {seed}")

    def test_window_edges_are_inclusive(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        same = np.ones(4, dtype=np.float32)
        records = [
            {"id": "a", "timestamp": base.isoformat(), "embedding": same},
            {"id": "b", "timestamp": (base + timedelta(hours=48)).isoformat(), "embedding": same},
            {"id": "c", "timestamp": (base + timedelta(hours=48, seconds=1)).isoformat(), "embedding": same},
        ]
        self.assertEqual(ids(link_entities(records)), [["a", "b"], ["c"]])

    def test_empty(self):
        self.assertEqual(link_entities([]), [])


if __name__ == "__main__":
    unittest.main()
