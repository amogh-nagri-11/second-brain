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

Each run builds a throwaway copy of the snapshot the way the app would: migrated
to the current schema, chunked and embedded with the current code, clustered --
then asks through the same retrieve() the app answers from. Two things are pinned
so runs compare like for like:
  - the text comes from the snapshot, so a model or chunking change is measured on
    identical input
  - "now" is questions.json's as_of, so recency scores don't drift between runs
"""

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from dateutil import parser as date_parser

from src.config.paths import data_dir, db_path
from src.entities.linking import link_entities
from src.pipeline import MAX_CONTEXT_RECORDS, retrieve
from src.storage.db import get_connection, load_all_records, load_clusters, save_clusters
from src.sync import reembed_if_stale

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


def build(workdir: Path):
    """The snapshot, brought up to date by the app's own code in a scratch copy.
    Returns (connection, clusters)."""
    copy = workdir / "eval.db"
    shutil.copy(eval_dir() / "snapshot.db", copy)
    conn = get_connection(copy)
    reembed_if_stale(conn, log=lambda _line: None, force=True)
    save_clusters(conn, link_entities(load_all_records(conn)))
    return conn, load_clusters(conn)


def _reciprocal_rank(ranked_ids: list[str], expected: set[str]) -> float:
    for rank, record_id in enumerate(ranked_ids, start=1):
        if record_id in expected:
            return 1.0 / rank
    return 0.0


def run(questions: dict) -> dict:
    now = date_parser.parse(questions["as_of"])
    with tempfile.TemporaryDirectory() as workdir:
        conn, clusters = build(Path(workdir))
        try:
            return _score(questions, conn, clusters, now)
        finally:
            conn.close()


def _score(questions: dict, conn, clusters: list[list[dict]], now) -> dict:
    records = [r for cluster in clusters for r in cluster]
    known_ids = {r["id"] for r in records}
    results = []

    for item in questions["questions"]:
        expected = set(item["expected"])
        missing = expected - known_ids
        if missing:
            raise SystemExit(f"{item['q']!r} expects ids not in the snapshot: {sorted(missing)}")

        context, ranked = retrieve(conn, item["q"], clusters, now=now)
        context_ids = {r["id"] for r in context}
        ranked_ids = [r["id"] for r in ranked]

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
