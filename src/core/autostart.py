"""Start the service at login, with whatever each OS uses for that.

    macOS    a launchd agent in ~/Library/LaunchAgents
    Linux    a systemd user unit in ~/.config/systemd/user
    Windows  a Task Scheduler task that runs at logon, in your own session

All three restart it if it crashes, and leave it stopped if you stop it. On
Windows it is deliberately a logon task and not a Windows Service: services run in
session 0, which has no access to your microphone or speakers.
"""

import getpass
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from src.config.paths import REPO_ROOT, data_dir, log_dir

SERVICE_ARGS = ["-m", "src", "serve", "--log-file"]
MENUBAR_ARGS = ["-m", "src.capture.menubar_app"]

LAUNCHD_SERVICE = "com.secondbrain.service"
# the label the old launch-agent script used, so installing replaces that agent
LAUNCHD_MENUBAR = "com.secondbrain.menubar"
SYSTEMD_UNIT = "second-brain.service"
WINDOWS_TASK = "SecondBrain"


def python_executable(windowless: bool = False) -> str:
    """The interpreter running this, which is the project's venv. On Windows the
    background copy uses pythonw, which opens no console window."""
    exe = Path(sys.executable)
    if windowless and sys.platform == "win32":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def shortcut_command() -> list[str]:
    """What a keyboard shortcut should run: runnable from any directory."""
    return [python_executable(windowless=True), str(REPO_ROOT / "src" / "__main__.py"), "record"]


# --- file contents ------------------------------------------------------------

def launchd_plist(label: str, args: list[str], python: str) -> str:
    arguments = "\n".join(f"        <string>{escape(a)}</string>" for a in [python, *args])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{arguments}
    </array>
    <!-- the repo root, so `python -m src` finds the package -->
    <key>WorkingDirectory</key>
    <string>{escape(str(REPO_ROOT))}</string>
    <key>RunAtLoad</key>
    <true/>
    <!-- restart after a crash, but a clean exit (stop, Quit) stays exited -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
    <!-- the app logs to its own file; this catches anything before that starts -->
    <key>StandardErrorPath</key>
    <string>{escape(str(log_dir() / f"{label}.stderr.log"))}</string>
</dict>
</plist>
"""


def systemd_unit(python: str) -> str:
    command = " ".join(f'"{part}"' for part in [python, *SERVICE_ARGS])
    return f"""[Unit]
Description=Second Brain

[Service]
ExecStart={command}
WorkingDirectory={REPO_ROOT}
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def windows_task_xml(python: str, user: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Second Brain service</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <!-- the default kills a task after 72 hours -->
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python)}</Command>
      <Arguments>{escape(" ".join(SERVICE_ARGS))}</Arguments>
      <WorkingDirectory>{escape(str(REPO_ROOT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


# --- per-OS commands ----------------------------------------------------------

def _run(argv: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=check)


class _Launchd:
    domain = f"gui/{os.getuid()}" if hasattr(os, "getuid") else ""

    def _plist(self, label: str) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    def _loaded(self, label: str) -> bool:
        return _run(["launchctl", "print", f"{self.domain}/{label}"], check=False).returncode == 0

    def _load(self, label: str, args: list[str]):
        path = self._plist(label)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._loaded(label):
            _run(["launchctl", "bootout", f"{self.domain}/{label}"], check=False)
        path.write_text(launchd_plist(label, args, python_executable()))
        _run(["launchctl", "bootstrap", self.domain, str(path)])

    def install(self, menubar: bool):
        self._load(LAUNCHD_SERVICE, SERVICE_ARGS)
        if menubar:
            self._load(LAUNCHD_MENUBAR, MENUBAR_ARGS)

    def uninstall(self):
        for label in (LAUNCHD_SERVICE, LAUNCHD_MENUBAR):
            if self._loaded(label):
                _run(["launchctl", "bootout", f"{self.domain}/{label}"], check=False)
            self._plist(label).unlink(missing_ok=True)

    def start(self):
        for label in (LAUNCHD_SERVICE, LAUNCHD_MENUBAR):
            if self._loaded(label):
                _run(["launchctl", "kickstart", f"{self.domain}/{label}"], check=False)
            elif self._plist(label).exists():
                _run(["launchctl", "bootstrap", self.domain, str(self._plist(label))], check=False)

    def stop(self):
        # SIGTERM makes the service exit cleanly, which KeepAlive leaves stopped
        for label in (LAUNCHD_MENUBAR, LAUNCHD_SERVICE):
            if self._loaded(label):
                _run(["launchctl", "kill", "SIGTERM", f"{self.domain}/{label}"], check=False)

    def installed(self) -> bool:
        return self._plist(LAUNCHD_SERVICE).exists()


class _Systemd:
    def _unit(self) -> Path:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / "systemd" / "user" / SYSTEMD_UNIT

    def install(self, menubar: bool):
        path = self._unit()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(systemd_unit(python_executable()))
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT])

    def uninstall(self):
        _run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], check=False)
        self._unit().unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"], check=False)

    def start(self):
        _run(["systemctl", "--user", "start", SYSTEMD_UNIT])

    def stop(self):
        _run(["systemctl", "--user", "stop", SYSTEMD_UNIT])

    def installed(self) -> bool:
        return self._unit().exists()


class _TaskScheduler:
    def _user(self) -> str:
        domain = os.environ.get("USERDOMAIN")
        user = os.environ.get("USERNAME") or getpass.getuser()
        return f"{domain}\\{user}" if domain else user

    def install(self, menubar: bool):
        xml_path = data_dir() / "SecondBrainTask.xml"
        # schtasks reads task XML as UTF-16
        xml_path.write_text(windows_task_xml(python_executable(windowless=True), self._user()), encoding="utf-16")
        _run(["schtasks", "/Create", "/TN", WINDOWS_TASK, "/XML", str(xml_path), "/F"])
        _run(["schtasks", "/Run", "/TN", WINDOWS_TASK])

    def uninstall(self):
        _run(["schtasks", "/End", "/TN", WINDOWS_TASK], check=False)
        _run(["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"], check=False)

    def start(self):
        _run(["schtasks", "/Run", "/TN", WINDOWS_TASK])

    def stop(self):
        # ends the process outright; the client treats the leftover service.json
        # as not running
        _run(["schtasks", "/End", "/TN", WINDOWS_TASK], check=False)

    def installed(self) -> bool:
        return _run(["schtasks", "/Query", "/TN", WINDOWS_TASK], check=False).returncode == 0


def backend():
    if sys.platform == "darwin":
        return _Launchd()
    if sys.platform == "win32":
        return _TaskScheduler()
    return _Systemd()


def hotkey_help() -> str:
    command = shortcut_command()
    quoted = " ".join(f'"{part}"' if " " in part else part for part in command)

    if sys.platform == "darwin":
        return f"""Bind a key to this command:

    {quoted}

macOS: open Shortcuts, make a new shortcut with a "Run Shell Script" action
containing the command above, then in the shortcut's details choose "Add Keyboard
Shortcut". No Accessibility permission is needed."""

    if sys.platform == "win32":
        program, script, action = command
        return f"""Bind a key to this command:

    {quoted}

PowerToys: Keyboard Manager > Remap a shortcut > action "Run Program",
    program    {program}
    arguments  "{script}" {action}
AutoHotkey v2, one line:
    F9::Run('"{program}" "{script}" {action}')"""

    return f"""Bind a key to this command:

    {quoted}

GNOME: Settings > Keyboard > View and Customise Shortcuts > Custom Shortcuts.
KDE: System Settings > Shortcuts > Add New > Command or Script.
Both work on Wayland."""
