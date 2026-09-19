import unittest
from unittest import mock

from src.output import speaker


class SpeechCommandTests(unittest.TestCase):
    def test_macos_passes_text_as_an_argument(self):
        argv, stdin = speaker.speech_command("-hi", 175, platform="darwin")
        self.assertEqual(argv, ["say", "-r", "175", "--", "-hi"])
        self.assertIsNone(stdin)

    def test_windows_sends_text_on_stdin(self):
        argv, stdin = speaker.speech_command('say "hi"; rm -rf', 175, platform="win32")
        self.assertEqual(argv[0], "powershell")
        self.assertIn("$s.Rate = 0;", argv[-1])
        self.assertNotIn("hi", argv[-1])
        self.assertEqual(stdin, 'say "hi"; rm -rf')

    def test_windows_rate_is_clamped(self):
        argv, _ = speaker.speech_command("x", 1000, platform="win32")
        self.assertIn("$s.Rate = 10;", argv[-1])

    def test_linux_prefers_espeak_ng(self):
        with mock.patch.object(speaker.shutil, "which", side_effect=lambda name: name == "espeak-ng"):
            argv, stdin = speaker.speech_command("hi", 175, platform="linux")
        self.assertEqual(argv, ["espeak-ng", "-s", "175", "--stdin"])
        self.assertEqual(stdin, "hi")

    def test_linux_without_an_engine_is_none(self):
        with mock.patch.object(speaker.shutil, "which", return_value=None):
            self.assertIsNone(speaker.speech_command("hi", 175, platform="linux"))


if __name__ == "__main__":
    unittest.main()
