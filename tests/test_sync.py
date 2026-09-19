import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from src import sync
from src.storage import db
from src.storage.types import ActivityRecord

NOW = datetime.now(timezone.utc)


def event(item_id, days_ago, title="Standup"):
    return ActivityRecord(id=item_id, source="calendar", kind="event",
                          timestamp=(NOW - timedelta(days=days_ago)).isoformat(), title=title, body="")


def commit(item_id, days_ago=2):
    return ActivityRecord(
        id=item_id, source="github", kind="commit", timestamp=(NOW - timedelta(days=days_ago)).isoformat(),
        title="dify [fix/x]: fix(api): repair", body="me/dify (fix/x)\n\nrepair",
        fields={"repo": "dify", "full_name": "me/dify", "branch": "fix/x", "merged": False, "sha": "abc"},
    )


def fake_chunks(title, body):
    return [(f"{title}\n{body}", np.ones(384, dtype=np.float32).tolist())]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.get_connection(Path(self._tmp.name) / "brain.db")
        self.addCleanup(self.conn.close)

        for patch in [
            mock.patch.object(sync, "embed_chunks", side_effect=fake_chunks),
            mock.patch.object(sync, "reembed_if_stale", return_value=False),
        ]:
            patch.start()
            self.addCleanup(patch.stop)

        self.calendar_events = []
        self.github_commits = []
        self.merged = set()
        sources = {
            "calendar": sync.Source(configured=lambda: True, fetch=lambda since: list(self.calendar_events),
                                    complete_window=True),
            "github": sync.Source(configured=lambda: True, fetch=lambda since: list(self.github_commits),
                                  after=sync._update_merged),
        }
        for patch in [
            mock.patch.object(sync, "SOURCES", sources),
            mock.patch.object(sync, "merged_commits", side_effect=lambda candidates: set(self.merged)),
        ]:
            patch.start()
            self.addCleanup(patch.stop)

    def run_sync(self):
        return sync.run_sync(self.conn, log=lambda _line: None)

    def live(self):
        return {r["id"] for r in db.load_all_records(self.conn)}

    def test_an_event_missing_from_its_window_is_deleted(self):
        self.calendar_events = [event("calendar:keep", 3), event("calendar:gone", 4)]
        self.run_sync()
        # a week of overlap before the last sync: both are inside the re-fetched window
        self.calendar_events = [event("calendar:keep", 3)]
        result = self.run_sync()
        self.assertEqual(self.live(), {"calendar:keep"})
        self.assertEqual(result["deleted"], 1)

    def test_older_events_outside_the_window_are_left_alone(self):
        self.calendar_events = [event("calendar:old", 60)]
        self.run_sync()
        self.calendar_events = []
        self.run_sync()
        self.assertEqual(self.live(), {"calendar:old"})

    def test_a_deleted_event_that_comes_back_is_restored(self):
        self.calendar_events = [event("calendar:e", 3)]
        self.run_sync()
        self.calendar_events = []
        self.run_sync()
        self.calendar_events = [event("calendar:e", 3)]
        self.run_sync()
        self.assertEqual(self.live(), {"calendar:e"})

    def test_github_is_never_reconciled(self):
        # a repo with no recent pushes is skipped by the fetch, so absence proves nothing
        self.github_commits = [commit("github:commit:abc")]
        self.run_sync()
        self.github_commits = []
        self.run_sync()
        self.assertEqual(self.live(), {"github:commit:abc"})

    def test_a_branch_commit_that_merged_is_relabelled(self):
        self.github_commits = [commit("github:commit:abc")]
        self.run_sync()
        self.merged = {"github:commit:abc"}
        self.github_commits = []
        result = self.run_sync()
        record = db.load_all_records(self.conn)[0]
        self.assertEqual(record["title"], "dify: fix(api): repair")
        self.assertTrue(record["fields"]["merged"])
        self.assertEqual(result["updated"], 1)

    def test_an_unconfigured_source_is_skipped_not_failed(self):
        sync.SOURCES["github"].configured = lambda: False
        result = self.run_sync()
        self.assertEqual(result["not_configured"], ["github"])
        self.assertEqual(result["failed"], [])


if __name__ == "__main__":
    unittest.main()
