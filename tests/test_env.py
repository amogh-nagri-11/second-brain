import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import env


class CredentialLookupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env_file = Path(self._tmp.name) / ".env"
        patches = [
            mock.patch.object(env, "_env_files", return_value=[self.env_file]),
            mock.patch.object(env, "_from_keyring", return_value=None),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("GITHUB_TOKEN", None)
        self.addCleanup(self._tmp.cleanup)

    def test_missing_is_none_not_an_error(self):
        self.assertIsNone(env.get_secret("GITHUB_TOKEN"))

    def test_require_names_the_fix(self):
        with self.assertRaisesRegex(env.MissingCredential, "src.config.env set GITHUB_TOKEN"):
            env.github_token()

    def test_env_file_is_the_fallback(self):
        self.env_file.write_text("GITHUB_TOKEN=from-file\n")
        self.assertEqual(env.lookup("GITHUB_TOKEN"), ("from-file", str(self.env_file)))

    def test_keychain_beats_env_file(self):
        self.env_file.write_text("GITHUB_TOKEN=from-file\n")
        with mock.patch.object(env, "_from_keyring", return_value="from-keychain"):
            self.assertEqual(env.lookup("GITHUB_TOKEN"), ("from-keychain", "keychain"))

    def test_environment_beats_everything(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "from-env"}):
            with mock.patch.object(env, "_from_keyring", return_value="from-keychain"):
                self.assertEqual(env.get_secret("GITHUB_TOKEN"), "from-env")


if __name__ == "__main__":
    unittest.main()
