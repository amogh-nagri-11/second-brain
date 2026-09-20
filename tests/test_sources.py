"""The source interface: what every source promises, and what adding one costs."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from src import sync
from src.ingestion.base import INITIAL_LOOKBACK_DAYS, Registry, Source
from src.ingestion.registry import sources
from src.storage import db
from src.storage.types import ActivityRecord

NOW = datetime.now(timezone.utc)


class RegistryTests(unittest.TestCase):
    def test_every_source_is_complete(self):
        for source in sources():
            with self.subTest(source.name):
                self.assertTrue(source.name)
                self.assertTrue(source.description, "a source says what it is, for the README and logs")
                self.assertTrue(source.kinds, "a source declares its kinds, or nothing can count it")
                self.assertTrue(callable(source.configured))
                self.assertTrue(callable(source.fetch))

    def test_names_are_unique(self):
        names = [source.name for source in sources()]
        self.assertEqual(len(names), len(set(names)), "the sync cursor is kept under the name")

    def test_kinds_reach_the_counting_tool(self):
        from src.synthesis.tools import KINDS

        self.assertEqual(KINDS, sources().kinds())

    def test_a_source_is_found_by_name(self):
        self.assertEqual(sources().get("github_prs").kinds, ("pr",))
        self.assertIsNone(sources().get("nothing-by-this-name"))


class NewSourceTests(unittest.TestCase):
    """A source that exists only here: if it syncs, adding a real one is the same
    work -- a module and a line in the registry, with sync untouched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.get_connection(Path(self._tmp.name) / "brain.db")
        self.addCleanup(self.conn.close)

        for patch in [
            mock.patch.object(sync, "embed_chunks",
                              side_effect=lambda title, body: [(title, np.ones(384, dtype=np.float32).tolist())]),
            mock.patch.object(sync, "reembed_if_stale", return_value=False),
        ]:
            patch.start()
            self.addCleanup(patch.stop)

        self.notes = [
            ActivityRecord(
                id="notes:1", source="notes", kind="note",
                timestamp=(NOW - timedelta(days=1)).isoformat(),
                title="Ideas for the retrieval eval", body="write the questions down first",
            )
        ]
        self.source = Source(
            name="notes",
            description="a folder of notes",
            configured=lambda: True,
            fetch=lambda since: list(self.notes),
            kinds=("note",),
        )

    def sync_with(self, *sources_):
        with mock.patch.object(sync, "sources", lambda: Registry(list(sources_))):
            return sync.run_sync(self.conn, log=lambda _line: None)

    def test_a_new_source_syncs_with_no_change_to_sync(self):
        result = self.sync_with(self.source)
        self.assertEqual(result["added"], 1)
        stored = db.load_all_records(self.conn)[0]
        self.assertEqual((stored["source"], stored["kind"]), ("notes", "note"))

    def test_its_kind_can_be_counted(self):
        self.sync_with(self.source)
        self.assertEqual(db.tally(self.conn, {"kind": "note"})["total"], 1)

    def test_its_first_sync_reaches_back_the_default(self):
        seen = {}
        self.source.fetch = lambda since: seen.setdefault("since", since) and []
        self.sync_with(self.source)
        reach = (NOW - seen["since"]).days
        self.assertAlmostEqual(reach, INITIAL_LOOKBACK_DAYS, delta=1)

    def test_one_source_failing_leaves_the_others_alone(self):
        def broken(_since):
            raise RuntimeError("the notes folder is gone")

        result = self.sync_with(Source(name="broken", configured=lambda: True, fetch=broken), self.source)
        self.assertEqual(result["failed"], ["broken"])
        self.assertEqual(result["added"], 1)

    def test_a_source_that_returns_its_whole_window_gets_deletions(self):
        self.source.complete_window = True
        self.sync_with(self.source)
        self.notes.clear()
        result = self.sync_with(self.source)
        self.assertEqual(result["deleted"], 1)

    def test_without_that_promise_nothing_is_deleted(self):
        self.sync_with(self.source)
        self.notes.clear()
        self.assertEqual(self.sync_with(self.source)["deleted"], 0)


if __name__ == "__main__":
    unittest.main()
