import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.config.paths import _copy_legacy_files


class LegacyCopyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.old, self.new = root / "repo", root / "data"
        self.old.mkdir()
        self.new.mkdir()

        with sqlite3.connect(self.old / "second-brain.db") as conn:
            conn.execute("CREATE TABLE t (x)")
            conn.execute("INSERT INTO t VALUES (1)")
        conn.close()
        (self.old / "token.json").write_text("old token")

    def tearDown(self):
        self._tmp.cleanup()

    def test_copies_and_leaves_the_originals(self):
        _copy_legacy_files(self.old, self.new, log=lambda _: None)

        conn = sqlite3.connect(self.new / "second-brain.db")
        self.assertEqual(conn.execute("SELECT x FROM t").fetchall(), [(1,)])
        conn.close()
        self.assertEqual((self.new / "token.json").read_text(), "old token")
        self.assertTrue((self.old / "second-brain.db").exists())
        self.assertTrue((self.old / "token.json").exists())

    def test_never_overwrites_what_is_already_there(self):
        (self.new / "token.json").write_text("new token")
        _copy_legacy_files(self.old, self.new, log=lambda _: None)
        self.assertEqual((self.new / "token.json").read_text(), "new token")

    def test_missing_files_are_skipped(self):
        (self.old / "token.json").unlink()
        _copy_legacy_files(self.old, self.new, log=lambda _: None)
        self.assertFalse((self.new / "token.json").exists())
        self.assertFalse((self.new / "credentials.json").exists())


if __name__ == "__main__":
    unittest.main()
