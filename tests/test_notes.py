"""Reading a folder of notes -- and never writing to it."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from src import sync
from src.ingestion import notes
from src.ingestion.base import Registry
from src.storage import db

NOW = datetime.now(timezone.utc)


class VaultTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name)
        patch = mock.patch.dict(os.environ, {"SECOND_BRAIN_NOTES": str(self.vault)})
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, name: str, text: str, modified: datetime | None = None) -> Path:
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if modified:
            os.utime(path, (modified.timestamp(), modified.timestamp()))
        return path

    def fetch(self):
        return notes.fetch_recent_notes(NOW - timedelta(days=1))


class ReadingTests(VaultTestCase):
    def test_a_note_becomes_a_record(self):
        self.write("Ideas/ranking.md", "# Why recency matters\n\nwithout it nothing means latest")
        record, = self.fetch()

        self.assertEqual(record.id, "note:Ideas/ranking.md")
        self.assertEqual((record.source, record.kind), ("notes", "note"))
        self.assertEqual(record.title, "Why recency matters")
        self.assertEqual(record.fields["folder"], "Ideas")
        self.assertIn("without it nothing means latest", record.body)

    def test_the_folder_is_part_of_the_text(self):
        # "Meetings/fleet sync" and "Ideas/fleet sync" are different notes
        self.write("Meetings/fleet sync.md", "talked about autoscaling")
        record, = self.fetch()
        self.assertIn("Meetings/fleet sync.md", record.body)

    def test_a_note_with_no_heading_is_titled_by_its_name(self):
        self.write("grocery list.md", "milk\nbread")
        self.assertEqual(self.fetch()[0].title, "grocery list")

    def test_front_matter_gives_the_title_date_and_tags(self):
        self.write("x.md", "---\ntitle: Retrieval notes\ndate: 2026-03-04\ntags: [search, eval]\n---\n\nbody here")
        record, = self.fetch()
        self.assertEqual(record.title, "Retrieval notes")
        self.assertTrue(record.timestamp.startswith("2026-03-04"))
        self.assertEqual(record.fields["tags"], ["search", "eval"])
        self.assertNotIn("---", record.body)

    def test_a_daily_note_is_dated_by_its_name(self):
        # not by when it was last touched, or every daily note would pile up on the
        # day the vault was synced
        self.write("Daily/2026-02-09.md", "stood up, shipped the parser", modified=NOW)
        self.assertTrue(self.fetch()[0].timestamp.startswith("2026-02-09"))

    def test_otherwise_the_file_time_is_used(self):
        when = NOW - timedelta(days=5)
        self.write("loose.md", "a thought", modified=when)
        self.assertTrue(self.fetch()[0].timestamp.startswith(when.strftime("%Y-%m-%d")))

    def test_machinery_and_noise_are_skipped(self):
        self.write(".obsidian/workspace.json", "{}")
        self.write("node_modules/readme.md", "not mine")
        self.write(".hidden.md", "hidden")
        self.write("image.png", "not text")
        self.write("empty.md", "   \n")
        self.write("huge.md", "x" * (notes.MAX_BYTES + 1))
        self.write("real.md", "# Real\n\nkeep me")

        self.assertEqual([r.title for r in self.fetch()], ["Real"])

    def test_every_note_comes_back_whatever_the_window(self):
        # deletions are worked out from what a fetch returns, so it must not filter
        # on modified time
        self.write("old.md", "written long ago", modified=NOW - timedelta(days=900))
        self.write("new.md", "written today", modified=NOW)
        self.assertEqual(len(self.fetch()), 2)

    def test_a_folder_it_cannot_read_says_so(self):
        # macOS returns "operation not permitted" for ~/Documents until Full Disk
        # Access is granted, and os.walk hides that by default
        self.write("note.md", "hello")
        with mock.patch.object(notes.os, "walk", side_effect=PermissionError(1, "Operation not permitted")):
            with self.assertRaises(notes.NotesFolderUnreadable) as caught:
                self.fetch()
        self.assertIn("Full Disk Access", str(caught.exception))

    def test_a_missing_folder_says_so(self):
        with mock.patch.dict(os.environ, {"SECOND_BRAIN_NOTES": str(self.vault / "nope")}):
            with self.assertRaises(notes.NotesFolderUnreadable):
                self.fetch()

    def test_nothing_is_written_to_the_vault(self):
        self.write("a.md", "# A\n\nbody")
        before = {p: p.stat().st_mtime_ns for p in self.vault.rglob("*") if p.is_file()}
        self.fetch()
        after = {p: p.stat().st_mtime_ns for p in self.vault.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "the source reads and never writes")


class FolderChoiceTests(VaultTestCase):
    def test_the_environment_wins(self):
        self.assertEqual(notes.notes_dir(), self.vault)

    def test_then_the_setting(self):
        with mock.patch.dict(os.environ, {"SECOND_BRAIN_NOTES": ""}), \
             mock.patch.object(notes, "setting", return_value="/tmp/from-settings"):
            self.assertEqual(notes.notes_dir(), Path("/tmp/from-settings"))

    def test_then_the_obsidian_vault(self):
        with mock.patch.dict(os.environ, {"SECOND_BRAIN_NOTES": ""}), \
             mock.patch.object(notes, "setting", return_value=None), \
             mock.patch.object(notes, "obsidian_vault", return_value=Path("/tmp/vault")):
            self.assertEqual(notes.notes_dir(), Path("/tmp/vault"))

    def test_with_nowhere_to_read_the_source_is_not_configured(self):
        with mock.patch.dict(os.environ, {"SECOND_BRAIN_NOTES": ""}), \
             mock.patch.object(notes, "setting", return_value=None), \
             mock.patch.object(notes, "obsidian_vault", return_value=None):
            self.assertFalse(notes.SOURCE.configured())


class SyncingNotesTests(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.conn = db.get_connection(self.vault.parent / "brain.db")
        self.addCleanup(self.conn.close)
        for patch in [
            mock.patch.object(sync, "embed_chunks",
                              side_effect=lambda title, body: [(title, np.ones(384, dtype=np.float32).tolist())]),
            mock.patch.object(sync, "reembed_if_stale", return_value=False),
            mock.patch.object(sync, "sources", lambda: Registry([notes.SOURCE])),
        ]:
            patch.start()
            self.addCleanup(patch.stop)

    def run_sync(self):
        return sync.run_sync(self.conn, log=lambda _line: None)

    def test_notes_are_stored_and_counted(self):
        self.write("a.md", "# A\n\none")
        self.write("b.md", "# B\n\ntwo")
        self.assertEqual(self.run_sync()["added"], 2)
        self.assertEqual(db.tally(self.conn, {"kind": "note"})["total"], 2)

    def test_an_edited_note_is_updated_not_duplicated(self):
        path = self.write("a.md", "# A\n\none")
        self.run_sync()
        path.write_text("# A\n\none, revised", encoding="utf-8")
        result = self.run_sync()
        self.assertEqual((result["added"], result["updated"]), (0, 1))
        self.assertEqual(db.tally(self.conn, {"kind": "note"})["total"], 1)

    def test_an_unchanged_note_is_not_re_embedded(self):
        self.write("a.md", "# A\n\none")
        self.run_sync()
        self.assertEqual(self.run_sync()["skipped"], 1)

    def test_a_deleted_note_leaves_the_store(self):
        path = self.write("a.md", "# A\n\none")
        self.run_sync()
        path.unlink()
        self.assertEqual(self.run_sync()["deleted"], 1)
        self.assertEqual(db.tally(self.conn, {"kind": "note"})["total"], 0)

    def test_even_an_old_note_deleted_today_leaves_the_store(self):
        # the reason notes reconcile over all of history: a note written years ago
        # falls outside the window a sync would otherwise check
        path = self.write("Daily/2019-04-04.md", "ancient thought")
        self.run_sync()
        path.unlink()
        self.assertEqual(self.run_sync()["deleted"], 1)


if __name__ == "__main__":
    unittest.main()
