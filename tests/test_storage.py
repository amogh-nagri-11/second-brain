import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.storage import db
from src.storage.types import ActivityRecord


def vector(*values):
    v = np.zeros(384, dtype=np.float32)
    v[: len(values)] = values
    return v


def schema1(path: Path):
    """A database as the app left it before items and chunks."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE activity_records (id TEXT PRIMARY KEY, source TEXT NOT NULL,
        timestamp TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, url TEXT,
        raw TEXT NOT NULL, embedding BLOB NOT NULL, cluster_id INTEGER)""")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    rows = [
        ("github:commit:abc", "github", "2026-08-18T12:12:00+00:00",
         "dify [fix/x]: fix(api): repair", "me/dify (fix/x)\n\nfix(api): repair", "u1", "{}", vector(1).tobytes(), 3),
        ("github:commit:def", "github", "2026-08-19T08:00:00+00:00",
         "sql-ledger: Phase 1", "me/sql-ledger (main)\n\nPhase 1", "u2", "{}", vector(0, 1).tobytes(), 3),
        ("calendar:e1", "calendar", "2024-12-24", "CodeChef", "", "u3",
         json.dumps({"attendees": [{"email": "a@x.com", "displayName": "Priya"}, {"email": "me@x.com", "self": True}],
                     "location": "Online"}), vector(0, 0, 1).tobytes(), None),
    ]
    conn.executemany("INSERT INTO activity_records VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "brain.db"
        schema1(self.path)
        self.conn = db.get_connection(self.path)
        self.addCleanup(self.conn.close)

    def test_keeps_a_copy_of_the_old_database(self):
        backup = sqlite3.connect(self.path.with_name("brain.schema1-backup.db"))
        self.assertEqual(backup.execute("SELECT count(*) FROM activity_records").fetchone()[0], 3)
        backup.close()

    def test_every_record_becomes_an_item_with_its_vector(self):
        records = {r["id"]: r for r in db.load_all_records(self.conn)}
        self.assertEqual(set(records), {"github:commit:abc", "github:commit:def", "calendar:e1"})
        self.assertTrue(np.allclose(records["github:commit:def"]["embedding"], vector(0, 1)))
        self.assertEqual(records["github:commit:abc"]["kind"], "commit")
        self.assertEqual(records["calendar:e1"]["kind"], "event")

    def test_commit_fields_are_rebuilt_from_title_and_body(self):
        fields = {r["id"]: r["fields"] for r in db.load_all_records(self.conn)}
        self.assertEqual(fields["github:commit:abc"],
                         {"repo": "dify", "full_name": "me/dify", "branch": "fix/x", "merged": False, "sha": "abc"})
        self.assertTrue(fields["github:commit:def"]["merged"])

    def test_event_fields_leave_out_yourself(self):
        event = next(r for r in db.load_all_records(self.conn) if r["id"] == "calendar:e1")
        self.assertEqual(event["fields"]["attendees"], ["Priya"])
        self.assertEqual(event["fields"]["location"], "Online")
        self.assertTrue(event["all_day"])
        self.assertEqual(event["timestamp"], "2024-12-24T00:00:00+00:00")

    def test_clusters_survive(self):
        clusters = db.load_clusters(self.conn)
        self.assertIn(sorted(["github:commit:abc", "github:commit:def"]), [sorted(r["id"] for r in c) for c in clusters])

    def test_connecting_again_changes_nothing(self):
        again = db.get_connection(self.path)
        self.assertEqual(len(db.load_all_records(again)), 3)
        again.close()


class ItemTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.get_connection(Path(self._tmp.name) / "brain.db")
        self.addCleanup(self.conn.close)

    def event(self, **overrides):
        values = dict(id="calendar:1", source="calendar", kind="event", timestamp="2026-09-01T10:00:00+05:30",
                      title="Standup", body="", fields={"attendees": ["Priya"]})
        values.update(overrides)
        return ActivityRecord(**values)

    def test_times_are_stored_in_utc(self):
        db.save_item(self.conn, self.event(), [("Standup", vector(1))])
        self.assertEqual(db.load_all_records(self.conn)[0]["timestamp"], "2026-09-01T04:30:00+00:00")

    def test_a_rescheduled_event_is_a_change(self):
        self.assertNotEqual(db.record_fingerprint(self.event()),
                            db.record_fingerprint(self.event(timestamp="2026-09-02T10:00:00+05:30")))

    def test_raw_payload_is_not_a_change(self):
        self.assertEqual(db.record_fingerprint(self.event(raw={"etag": "1"})),
                         db.record_fingerprint(self.event(raw={"etag": "2"})))

    def test_saving_again_replaces_the_chunks(self):
        db.save_item(self.conn, self.event(), [("a", vector(1)), ("b", vector(0, 1))])
        db.save_item(self.conn, self.event(title="Retro"), [("c", vector(0, 0, 1))])
        record = db.load_all_records(self.conn)[0]
        self.assertEqual(record["title"], "Retro")
        self.assertEqual(record["chunk_embeddings"].shape, (1, 384))

    def test_deleted_items_are_hidden_until_seen_again(self):
        db.save_item(self.conn, self.event(), [("a", vector(1))])
        db.mark_deleted(self.conn, ["calendar:1"])
        self.assertEqual(db.load_all_records(self.conn), [])
        self.assertEqual(db.existing_fingerprints(self.conn), {})
        db.save_item(self.conn, self.event(), [("a", vector(1))])
        self.assertEqual(len(db.load_all_records(self.conn)), 1)


if __name__ == "__main__":
    unittest.main()
