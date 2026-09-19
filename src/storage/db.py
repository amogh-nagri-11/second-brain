"""The SQLite store.

    items       one row per thing that happened, whatever the source
    chunks      the pieces of an item's text that get embedded, with their vectors
    sync_state  how far each source has been synced
    meta        small settings, e.g. which model produced the stored vectors
    history     questions asked and what came back

Times are stored normalised to UTC (`occurred_at`), so everything sorts and filters
against everything else; all-day entries keep a flag, since their "time" is only a
date. Deleted items stay, marked by `deleted_at`, so a later sync can tell a
deletion from something never seen.

The schema is versioned with PRAGMA user_version and migrated in place on connect.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dateutil import parser as date_parser

from src.config.paths import db_path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT,
    fields TEXT NOT NULL DEFAULT '{}',
    raw TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL,
    cluster_id INTEGER,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS items_by_time ON items (occurred_at);
CREATE INDEX IF NOT EXISTS items_by_source_time ON items (source, occurred_at);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_by_item ON chunks (item_id);

-- remembers how far each source has been ingested, so a sync only pulls new stuff
CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT PRIMARY KEY,
    last_synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- questions asked and what came back, so the window and the menu still have them
-- after a restart
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at REAL NOT NULL,
    question TEXT NOT NULL,
    spoken TEXT NOT NULL,
    written TEXT NOT NULL,
    via TEXT NOT NULL
);
"""

KIND_BY_SOURCE = {"github": "commit", "calendar": "event"}


def get_connection(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        if _has_table(conn, "activity_records"):
            _back_up_before_migrating(conn, Path(path or db_path()))
        with conn:
            conn.executescript(SCHEMA)
            if _has_table(conn, "activity_records"):
                _migrate_from_activity_records(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _back_up_before_migrating(conn: sqlite3.Connection, path: Path):
    """The migration drops the old table, so keep a copy of the database as it was
    -- once, beside the original."""
    backup = path.with_name(f"{path.stem}.schema1-backup{path.suffix}")
    if backup.exists():
        return
    target = sqlite3.connect(backup)
    try:
        conn.backup(target)
    finally:
        target.close()
    print(f"[db] migrating to schema {SCHEMA_VERSION}; the old database is kept at {backup}")


# --- normalising -------------------------------------------------------------

def normalise_time(timestamp: str) -> tuple[str, bool]:
    """(UTC ISO time, all day?). A bare date is an all-day entry, pinned to midnight
    UTC on that date so it still sorts and filters by day."""
    all_day = len(timestamp) == 10
    moment = date_parser.parse(timestamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(), all_day


def fingerprint(title: str, body: str, occurred_at: str, url: str | None, fields: dict) -> str:
    """What decides whether an item changed. Everything a question could see --
    a rescheduled meeting with the same title is a change -- and nothing from the
    raw payload, whose etags and update stamps move on every fetch."""
    payload = json.dumps([title, body, occurred_at, url, fields], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _commit_fields_from_stored(title: str, body: str, record_id: str) -> dict:
    """Rebuild a commit's fields from what the old table kept: the title reads
    "repo: subject" or "repo [branch]: subject", and the body starts with
    "owner/repo (branch)"."""
    label = title.split(": ", 1)[0]
    repo = label.split(" [", 1)[0]
    first_line = body.split("\n", 1)[0]
    full_name, _, rest = first_line.partition(" (")
    branch = rest.rstrip(")") if rest else None
    return {
        "repo": repo,
        "full_name": full_name or None,
        "branch": branch,
        # topic-branch commits are the ones labelled with their branch
        "merged": " [" not in label,
        "sha": record_id.rsplit(":", 1)[-1],
    }


def event_fields(event: dict) -> dict:
    attendees = [
        a.get("displayName") or a.get("email")
        for a in event.get("attendees", [])
        if not a.get("self") and (a.get("displayName") or a.get("email"))
    ]
    organizer = event.get("organizer", {})
    return {
        "attendees": attendees,
        "location": event.get("location"),
        "organizer": None if organizer.get("self") else (organizer.get("displayName") or organizer.get("email")),
        "status": event.get("status"),
    }


def _migrate_from_activity_records(conn: sqlite3.Connection):
    """Schema 1 kept everything in one table with one vector per record. Each row
    becomes an item with a single chunk; its vector moves across unchanged."""
    rows = conn.execute(
        "SELECT id, source, timestamp, title, body, url, raw, embedding, cluster_id FROM activity_records ORDER BY rowid"
    ).fetchall()

    for record_id, source, timestamp, title, body, url, raw, embedding, cluster_id in rows:
        occurred_at, all_day = normalise_time(timestamp)
        raw_dict = json.loads(raw or "{}")
        if source == "github":
            fields = _commit_fields_from_stored(title, body, record_id)
        else:
            fields = event_fields(raw_dict)

        conn.execute(
            """INSERT OR REPLACE INTO items
               (id, source, kind, occurred_at, all_day, title, body, url, fields, raw, fingerprint, cluster_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record_id, source, KIND_BY_SOURCE.get(source, source), occurred_at, int(all_day),
                title, body, url, json.dumps(fields), raw or "{}",
                fingerprint(title, body, occurred_at, url, fields), cluster_id,
            ),
        )
        conn.execute(
            "INSERT INTO chunks (item_id, seq, text, embedding) VALUES (?, 0, ?, ?)",
            (record_id, f"{title}\n{body}", embedding),
        )

    # cluster membership comes across as it was, so answers don't change until the
    # next sync rebuilds it anyway
    conn.execute("DROP TABLE activity_records")


# --- items ------------------------------------------------------------------

def save_item(conn: sqlite3.Connection, record, chunks: list[tuple[str, list[float]]]):
    """Insert or replace an item and all of its chunks."""
    occurred_at, all_day = normalise_time(record.timestamp)
    all_day = all_day or record.all_day

    with conn:
        conn.execute(
            """INSERT INTO items
               (id, source, kind, occurred_at, all_day, title, body, url, fields, raw, fingerprint, deleted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)
               ON CONFLICT (id) DO UPDATE SET
                 source = excluded.source, kind = excluded.kind, occurred_at = excluded.occurred_at,
                 all_day = excluded.all_day, title = excluded.title, body = excluded.body,
                 url = excluded.url, fields = excluded.fields, raw = excluded.raw,
                 fingerprint = excluded.fingerprint, deleted_at = NULL, cluster_id = NULL""",
            (
                record.id, record.source, record.kind, occurred_at, int(all_day), record.title,
                record.body, record.url, json.dumps(record.fields), json.dumps(record.raw),
                record_fingerprint(record),
            ),
        )
        replace_chunks(conn, record.id, chunks)


def record_fingerprint(record) -> str:
    occurred_at, _ = normalise_time(record.timestamp)
    return fingerprint(record.title, record.body, occurred_at, record.url, record.fields)


def replace_chunks(conn: sqlite3.Connection, item_id: str, chunks: list[tuple[str, list[float]]]):
    conn.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
    conn.executemany(
        "INSERT INTO chunks (item_id, seq, text, embedding) VALUES (?,?,?,?)",
        [
            (item_id, seq, text, np.asarray(vector, dtype=np.float32).tobytes())
            for seq, (text, vector) in enumerate(chunks)
        ],
    )


def mark_deleted(conn: sqlite3.Connection, item_ids: list[str]):
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.executemany(
            "UPDATE items SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
            [(now, item_id) for item_id in item_ids],
        )


def existing_fingerprints(conn: sqlite3.Connection) -> dict[str, str]:
    """id -> fingerprint for everything stored and not deleted, so a sync can skip
    re-embedding what it has already seen unchanged."""
    return dict(conn.execute("SELECT id, fingerprint FROM items WHERE deleted_at IS NULL"))


def item_texts(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(id, title, body) for every item, for re-chunking and re-embedding."""
    return conn.execute("SELECT id, title, body FROM items").fetchall()


def _load(conn: sqlite3.Connection, order: str) -> list[tuple[dict, int | None]]:
    vectors: dict[str, list[np.ndarray]] = {}
    for item_id, embedding in conn.execute("SELECT item_id, embedding FROM chunks ORDER BY item_id, seq"):
        vectors.setdefault(item_id, []).append(np.frombuffer(embedding, dtype=np.float32))

    loaded = []
    for row in conn.execute(
        """SELECT id, source, kind, occurred_at, all_day, title, body, url, fields, cluster_id
           FROM items WHERE deleted_at IS NULL ORDER BY """ + order
    ):
        chunk_vectors = vectors.get(row[0])
        if not chunk_vectors:
            continue
        stacked = np.stack(chunk_vectors)
        # one vector standing for the whole item, for clustering: the mean of its
        # pieces, back on the unit sphere
        mean = stacked.mean(axis=0)
        record = {
            "id": row[0],
            "source": row[1],
            "kind": row[2],
            "timestamp": row[3],
            "all_day": bool(row[4]),
            "title": row[5],
            "body": row[6],
            "url": row[7],
            "fields": json.loads(row[8]),
            "embedding": mean / max(float(np.linalg.norm(mean)), 1e-12),
            "chunk_embeddings": stacked,
        }
        loaded.append((record, row[9]))
    return loaded


def load_all_records(conn: sqlite3.Connection) -> list[dict]:
    # insertion order: clustering is greedy, so the order it sees records in is
    # part of its result
    return [record for record, _cluster in _load(conn, "rowid")]


def load_clusters(conn: sqlite3.Connection) -> list[list[dict]]:
    """Read pre-computed clusters. Items not yet clustered come back as singletons."""
    grouped: dict[int, list[dict]] = {}
    singletons: list[list[dict]] = []
    for record, cluster_id in _load(conn, "cluster_id, rowid"):
        if cluster_id is None:
            singletons.append([record])
        else:
            grouped.setdefault(cluster_id, []).append(record)
    return list(grouped.values()) + singletons


def save_clusters(conn: sqlite3.Connection, clusters: list[list[dict]]):
    """Persist cluster membership so queries never have to re-cluster."""
    with conn:
        conn.execute("UPDATE items SET cluster_id = NULL")
        conn.executemany(
            "UPDATE items SET cluster_id = ? WHERE id = ?",
            [(index, record["id"]) for index, cluster in enumerate(clusters) for record in cluster],
        )
        bump_clusters_version(conn)


def get_clusters_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'clusters_version'").fetchone()
    return int(row[0]) if row else 0


def bump_clusters_version(conn: sqlite3.Connection):
    conn.execute(
        """
            INSERT INTO meta (key, value) VALUES ('clusters_version', '1')
            ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
        """
    )


# --- sync state, meta, history -----------------------------------------------

def get_last_synced_at(conn: sqlite3.Connection, source: str) -> str | None:
    row = conn.execute("SELECT last_synced_at FROM sync_state WHERE source = ?", (source,)).fetchone()
    return row[0] if row else None


def set_last_synced_at(conn: sqlite3.Connection, source: str, timestamp: str):
    conn.execute(
        """
            INSERT INTO sync_state (source, last_synced_at) VALUES (?, ?)
            ON CONFLICT(source) DO UPDATE SET last_synced_at = excluded.last_synced_at
        """,
        (source, timestamp),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def add_history(conn: sqlite3.Connection, asked_at: float, question: str, spoken: str, written: str, via: str):
    conn.execute(
        "INSERT INTO history (asked_at, question, spoken, written, via) VALUES (?,?,?,?,?)",
        (asked_at, question, spoken, written, via),
    )
    conn.commit()


def recent_history(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """Newest first."""
    rows = conn.execute(
        "SELECT asked_at, question, spoken, written, via FROM history ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {"asked_at": r[0], "question": r[1], "spoken": r[2], "written": r[3], "via": r[4]}
        for r in rows
    ]
