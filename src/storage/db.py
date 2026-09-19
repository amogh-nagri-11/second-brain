import sqlite3
import json
import numpy as np

from src.config.paths import db_path

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_records (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            url TEXT,
            raw TEXT NOT NULL,
            embedding BLOB NOT NULL,
            cluster_id INTEGER
        )
    """)

    # remembers how far each source has been ingested, so a sync only pulls new stuff
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            source TEXT PRIMARY KEY,
            last_synced_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    _migrate(conn)
    conn.commit()

    return conn

def _migrate(conn: sqlite3.Connection):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(activity_records)")}
    if "cluster_id" not in columns:
        conn.execute("ALTER TABLE activity_records ADD COLUMN cluster_id INTEGER")

def save_record(conn: sqlite3.Connection, record, embeddings: list[float]):
    embeddings_bytes = np.array(embeddings, dtype=np.float32).tobytes()

    conn.execute(
        """
            INSERT OR REPLACE INTO activity_records
            (id, source, timestamp, title, body, url, raw, embedding, cluster_id)
            VALUES (?,?,?,?,?,?,?,?,NULL)
        """,
        (
            record.id, record.source, record.timestamp, record.title, record.body, record.url, json.dumps(record.raw), embeddings_bytes
        )
    )
    conn.commit()

def _row_to_record(row) -> dict:
    return {
        "id": row[0],
        "source": row[1],
        "timestamp": row[2],
        "title": row[3],
        "body": row[4],
        "url": row[5],
        "raw": json.loads(row[6]),
        "embedding": np.frombuffer(row[7], dtype=np.float32),
    }

def load_all_records(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT id, source, timestamp, title, body, url, raw, embedding
        FROM activity_records
    """).fetchall()
    return [_row_to_record(row) for row in rows]

def existing_fingerprints(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """id -> (title, body) for everything already stored, so a sync can skip re-embedding
    records it has already seen unchanged."""
    return {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT id, title, body FROM activity_records")
    }

def save_clusters(conn: sqlite3.Connection, clusters: list[list[dict]]):
    """Persist cluster membership so queries never have to re-cluster."""
    conn.execute("UPDATE activity_records SET cluster_id = NULL")
    conn.executemany(
        "UPDATE activity_records SET cluster_id = ? WHERE id = ?",
        [(index, record["id"]) for index, cluster in enumerate(clusters) for record in cluster],
    )
    bump_clusters_version(conn)
    conn.commit()

def load_clusters(conn: sqlite3.Connection) -> list[list[dict]]:
    """Read pre-computed clusters. Records not yet clustered come back as singletons."""
    rows = conn.execute("""
        SELECT id, source, timestamp, title, body, url, raw, embedding, cluster_id
        FROM activity_records
        ORDER BY cluster_id
    """).fetchall()

    grouped: dict[int, list[dict]] = {}
    singletons: list[list[dict]] = []

    for row in rows:
        record = _row_to_record(row)
        cluster_id = row[8]
        if cluster_id is None:
            singletons.append([record])
        else:
            grouped.setdefault(cluster_id, []).append(record)

    return list(grouped.values()) + singletons

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
