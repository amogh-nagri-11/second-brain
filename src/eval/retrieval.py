"""Does retrieval still find the right records?

Runs a fixed set of questions, each with the ids of the records a correct answer
needs, through the same ranking the app uses -- no LLM, so a run is free,
deterministic and takes seconds. Meant to be run before and after anything that
could move retrieval (the embedding model, chunking, the scoring weights) so a
change is shown to help, or at least not hurt, rather than assumed to.

    python -m src.eval.retrieval --snapshot        freeze the current database
    python -m src.eval.retrieval --label torch     run, and save as a baseline
    python -m src.eval.retrieval --label onnx --compare torch

Everything lives in <app folder>/eval, not the repo -- the questions and the
snapshot are about your own commits and calendar:

    questions.json   {"as_of": iso time, "questions": [{"q", "expected": [ids]}]}
    snapshot.db      the records the questions were written against
    runs/<label>.json

Two things are pinned so runs compare like for like:
  - the records come from the snapshot and are re-embedded with whatever model the
    code currently uses, so a model swap is measured on identical text
  - "now" is questions.json's as_of, so recency scores don't drift between runs
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from dateutil import parser as date_parser

from src.config.paths import data_dir, db_path
from src.embeddings.provider import get_embedder
from src.entities.linking import link_entities
from src.pipeline import MAX_CONTEXT_RECORDS, TOP_K_CLUSTERS, _context_records
from src.retrieval.search import score_records, search

TOP_K = 10


def eval_dir() -> Path:
    path = data_dir() / "eval"
    (path / "runs").mkdir(parents=True, exist_ok=True)
    return path


def take_snapshot():
    target = eval_dir() / "snapshot.db"
    with sqlite3.connect(db_path()) as source, sqlite3.connect(target) as copy:
        source.backup(copy)
    source.close()
    copy.close()
    print(f"snapshot written to {target}")


def load_snapshot_records() -> list[dict]:
    conn = sqlite3.connect(eval_dir() / "snapshot.db")
    rows = conn.execute("SELECT id, source, timestamp, title, body FROM activity_records").fetchall()
    conn.close()
    return [
        {"id": r[0], "source": r[1], "timestamp": r[2], "title": r[3], "body": r[4], "url": None, "raw": {}}
        for r in rows
    ]


def _reciprocal_rank(ranked_ids: list[str], expected: set[str]) -> float:
    for rank, record_id in enumerate(ranked_ids, start=1):
        if record_id in expected:
            return 1.0 / rank
    return 0.0


def run(questions: dict) -> dict:
    now = date_parser.parse(questions["as_of"])
    records = load_snapshot_records()

    embedder = get_embedder()
    vectors = embedder.embed_batch([f"{r['title']}\n{r['body']}" for r in records])
    for record, vector in zip(records, vectors):
        record["embedding"] = vector
    clusters = link_entities(records)

    known_ids = {r["id"] for r in records}
    results = []

    for item in questions["questions"]:
        expected = set(item["expected"])
        missing = expected - known_ids
        if missing:
            raise SystemExit(f"{item['q']!r} expects ids not in the snapshot: {sorted(missing)}")

        query_embedding = embedder.embed(item["q"])
        ranked = score_records(query_embedding, item["q"], records, now=now)
        top_clusters = search(query_embedding, item["q"], clusters, top_k=TOP_K_CLUSTERS, now=now)
        context_ids = {r["id"] for r in _context_records(top_clusters, ranked)}
        ranked_ids = [r["id"] for r, _score in ranked]

        results.append({
            "q": item["q"],
            "expected": len(expected),
            f"recall@{TOP_K}": len(expected & set(ranked_ids[:TOP_K])) / len(expected),
            # what the model is actually shown -- a count is only right if every
            # match makes it this far
            "recall@context": len(expected & context_ids) / len(expected),
            "rr": _reciprocal_rank(ranked_ids, expected),
            "missed": sorted(expected - context_ids),
        })

    def mean(key):
        return sum(r[key] for r in results) / len(results)

    return {
        "as_of": questions["as_of"],
        "records": len(records),
        "context_budget": MAX_CONTEXT_RECORDS,
        "summary": {
            f"recall@{TOP_K}": mean(f"recall@{TOP_K}"),
            "recall@context": mean("recall@context"),
            "mrr": mean("rr"),
            "complete": sum(r["recall@context"] == 1.0 for r in results),
            "questions": len(results),
        },
        "results": results,
    }


def _print(report: dict, baseline: dict | None):
    before = {r["q"]: r for r in baseline["results"]} if baseline else {}

    print(f"{'recall@10':>9} {'context':>7} {'rr':>5}  question")
    for r in report["results"]:
        mark = ""
        if r["q"] in before:
            old = before[r["q"]]
            delta = r["recall@context"] - old["recall@context"]
            if delta or r["rr"] != old["rr"]:
                mark = f"   (was {old[f'recall@{TOP_K}']:.2f} {old['recall@context']:.2f} {old['rr']:.2f})"
        print(f"{r[f'recall@{TOP_K}']:9.2f} {r['recall@context']:7.2f} {r['rr']:5.2f}  {r['q']}{mark}")

    s = report["summary"]
    print(
        f"\nrecall@10 {s['recall@10']:.3f}   recall@context {s['recall@context']:.3f}   "
        f"MRR {s['mrr']:.3f}   complete {s['complete']}/{s['questions']}"
    )
    if baseline:
        b = baseline["summary"]
        print(
            f"baseline  {b['recall@10']:.3f}                  {b['recall@context']:.3f}       "
            f"{b['mrr']:.3f}            {b['complete']}/{b['questions']}"
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", action="store_true", help="freeze the current database and exit")
    parser.add_argument("--label", help="save this run as runs/<label>.json")
    parser.add_argument("--compare", help="label of an earlier run to diff against")
    args = parser.parse_args(argv)

    if args.snapshot:
        take_snapshot()
        return 0

    questions = json.loads((eval_dir() / "questions.json").read_text())
    report = run(questions)

    baseline = None
    if args.compare:
        baseline = json.loads((eval_dir() / "runs" / f"{args.compare}.json").read_text())

    _print(report, baseline)

    if args.label:
        out = eval_dir() / "runs" / f"{args.label}.json"
        out.write_text(json.dumps(report, indent=2))
        print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
