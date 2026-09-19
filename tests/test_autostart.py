import plistlib
import unittest
import xml.etree.ElementTree as ET

from src.core import autostart

PYTHON = "/path with space/python"


class GeneratedFileTests(unittest.TestCase):
    def test_plist_is_valid_and_runs_the_service(self):
        plist = plistlib.loads(autostart.launchd_plist("com.x", autostart.SERVICE_ARGS, PYTHON).encode())
        self.assertEqual(plist["ProgramArguments"], [PYTHON, "-m", "src", "serve", "--log-file"])
        self.assertEqual(plist["WorkingDirectory"], str(autostart.REPO_ROOT))
        # a clean exit (stop, Quit) must stay stopped
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})

    def test_plist_escapes_paths(self):
        plist = plistlib.loads(autostart.launchd_plist("com.x", ["a&b"], "/p<y>").encode())
        self.assertEqual(plist["ProgramArguments"], ["/p<y>", "a&b"])

    def test_systemd_quotes_the_command(self):
        unit = autostart.systemd_unit(PYTHON)
        self.assertIn(f'ExecStart="{PYTHON}" "-m" "src" "serve" "--log-file"', unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_windows_task_runs_forever_in_the_users_session(self):
        xml = autostart.windows_task_xml(r"C:\venv\pythonw.exe", r"PC\me & you")
        root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find(".//t:ExecutionTimeLimit", ns).text, "PT0S")
        self.assertEqual(root.find(".//t:LogonType", ns).text, "InteractiveToken")
        self.assertEqual(root.find(".//t:Exec/t:Command", ns).text, r"C:\venv\pythonw.exe")
        self.assertEqual(root.find(".//t:Exec/t:Arguments", ns).text, "-m src serve --log-file")
        self.assertEqual(root.find(".//t:LogonTrigger/t:UserId", ns).text, r"PC\me & you")

    def test_shortcut_runs_the_file_so_any_directory_works(self):
        command = autostart.shortcut_command()
        self.assertTrue(command[1].endswith("__main__.py"))
        self.assertEqual(command[2], "record")


if __name__ == "__main__":
    unittest.main()
