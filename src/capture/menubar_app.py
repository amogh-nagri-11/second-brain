"""The macOS menubar: a small client of the service.

Everything it shows comes from the service's event stream, and everything it does
is a request to the service -- so it loads no models, records nothing itself, and
needs no Accessibility permission. The hotkey now belongs to the OS (see
`python -m src service hotkey`).
"""

import re
import subprocess
import sys
import textwrap
import threading
import time

import pyperclip
import rumps

from src.config.paths import ICONS_DIR, REPO_ROOT
from src.core import client

IDLE_ICON = str(ICONS_DIR / "idle.png")
RECORDING_ICON = str(ICONS_DIR / "recording.png")

# the menu is rebuilt on a timer rather than from the event thread, so AppKit is
# only ever touched from the main thread
MENU_REFRESH_SECONDS = 1
# how long to wait before reconnecting to a service that went away
RECONNECT_SECONDS = 3
RECENT_TITLE_CHARS = 45
TRANSCRIPT_SEPARATOR = "\n\n" + "-" * 52 + "\n\n"

# the answer is shown in the dropdown itself, one menu row per wrapped line
ANSWER_WIDTH = 62
MAX_ANSWER_LINES = 18


def _ago(moment: float | None) -> str:
    if moment is None:
        return "never"
    seconds = int(time.time() - moment)
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _shorten(text: str, limit: int = RECENT_TITLE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _answer_lines(written: str) -> list[str]:
    """Wrap the written answer into menu rows.

    Bold markers and heading hashes are dropped -- they mean nothing in a menu and
    just add noise -- but the bullets stay, since they are what makes a list
    readable at a glance.
    """
    lines: list[str] = []

    for raw in written.splitlines():
        raw = raw.strip()
        if not raw:
            continue

        raw = re.sub(r"\*\*(.+?)\*\*", r"\1", raw)
        raw = re.sub(r"^#{1,6}\s*", "", raw)

        wrapped = textwrap.wrap(raw, ANSWER_WIDTH, subsequent_indent="    ") or [raw]
        for line in wrapped:
            if len(lines) >= MAX_ANSWER_LINES:
                lines.append("…  (use Copy for Docs for the rest)")
                return lines
            lines.append(line)

    return lines


def _disabled(title: str) -> rumps.MenuItem:
    item = rumps.MenuItem(title)
    item.set_callback(None)
    return item


def _in_background(action):
    """Run a request off the main thread, so a slow service never freezes the menu."""
    def run(*_args):
        def call():
            try:
                action()
            except (client.ServiceNotRunning, RuntimeError) as error:
                print(f"[menubar] {error}")
        threading.Thread(target=call, daemon=True).start()
    return run


class SecondBrainApp(rumps.App):
    def __init__(self):
        super().__init__("", icon=IDLE_ICON, quit_button="Quit", template=True)

        # written by the event thread, read by the main-thread timer
        self._lock = threading.Lock()
        self._state: dict | None = None
        self._connected = False
        self._notifications: list[tuple[str, str]] = []
        self._rendered_key = None

        self._build_items()
        self._owns_quit_button = False
        self._rebuild_menu()
        # from here on the menu is ours to rebuild, quit button included
        self._owns_quit_button = True

        threading.Thread(target=self._listen, daemon=True).start()
        rumps.Timer(self._refresh_menu, MENU_REFRESH_SECONDS).start()

    # --- the service's events -------------------------------------------------

    def _listen(self):
        while True:
            try:
                for event in client.events():
                    self._on_event(event)
            except (client.ServiceNotRunning, OSError, ValueError):
                pass
            with self._lock:
                self._connected = False
            time.sleep(RECONNECT_SECONDS)

    def _on_event(self, event: dict):
        with self._lock:
            self._connected = True
            self._state = event["state"]

            if event["type"] == "answer" and event["entry"]["via"] == "voice":
                entry = event["entry"]
                self._notifications.append((entry["question"], entry["spoken"]))
            elif event["type"] == "synced" and event.get("requested"):
                result = event["result"]
                if "error" in result:
                    self._notifications.append(("Sync failed", result["error"]))
                else:
                    self._notifications.append(
                        ("Sync complete", f"{result['added']} new, {result['updated']} updated")
                    )

    # --- the menu -------------------------------------------------------------

    def _build_items(self):
        """The long-lived items. These are re-added on every rebuild rather than
        recreated, so the references and callbacks here stay valid."""
        self._window_item = rumps.MenuItem("Open Window", callback=_in_background(self._open_window))
        self._record_item = rumps.MenuItem(
            "Start Recording", callback=_in_background(lambda: client.call("POST", "/api/record"))
        )
        # greyed out until there is something to stop
        self._speaking_item = rumps.MenuItem("Not Speaking")
        self._copy_item = rumps.MenuItem("Copy for Docs", callback=self.copy_written)
        self._recent_item = rumps.MenuItem("Recent")
        self._transcript_item = rumps.MenuItem("Show Transcript…", callback=self.show_transcript)
        self._sync_item = rumps.MenuItem("Sync Now")
        self._start_item = rumps.MenuItem("Start the Service", callback=self.start_service)

    def _snapshot(self) -> tuple[dict | None, bool]:
        with self._lock:
            return self._state, self._connected

    def _rebuild_menu(self):
        """Rebuild the whole dropdown, latest answer included.

        Keys are explicit because two wrapped lines can read identically and the menu
        is a dict keyed by title -- a duplicate would silently drop the second one.
        Only called when the answer or the connection changed; the per-second
        refresh just retitles the few items that move.
        """
        state, connected = self._snapshot()
        history = state["history"] if state and connected else []

        self.menu.clear()

        if not connected:
            self.menu["down"] = _disabled("The service isn't running")
            self.menu["start"] = self._start_item
        else:
            self.menu["window"] = self._window_item
            self.menu["record"] = self._record_item
            self.menu["speaking"] = self._speaking_item
            self.menu["sep-answer"] = rumps.separator

            if history:
                latest = history[0]
                self.menu["heard"] = _disabled(f"heard: {_shorten(latest['question'], ANSWER_WIDTH)}")
                self.menu["sep-heard"] = rumps.separator
                for index, line in enumerate(_answer_lines(latest["written"] or latest["spoken"])):
                    self.menu[f"ans-{index}"] = _disabled(line)
                self.menu["copy"] = self._copy_item
            else:
                self.menu["heard"] = _disabled("Nothing asked yet — use your shortcut to ask")

            self.menu["sep-nav"] = rumps.separator
            self.menu["recent"] = self._recent_item
            self.menu["transcript"] = self._transcript_item
            self.menu["sep-sync"] = rumps.separator
            self.menu["sync"] = self._sync_item

        # rumps appends the quit button itself when run() is called, and its add()
        # always calls addItem_ -- the duplicate check runs against a sentinel, not
        # the real key -- so having it here first makes run() throw "already is in
        # another menu". Leave it out of the first build and put it back on every
        # rebuild after that, or clear() would take away the only way to quit.
        if self._owns_quit_button and self.quit_button is not None:
            self.menu[self.quit_button.title] = self.quit_button

        if connected:
            self._render_recent(history)

    def _render_recent(self, history: list[dict]):
        # the submenu's NSMenu is created lazily on first add(), and clear() blows up
        # on the None it starts as -- so only clear once something is actually in it
        if len(self._recent_item):
            self._recent_item.clear()

        if not history:
            self._recent_item.add(_disabled("Nothing asked yet"))
            return

        for index, entry in enumerate(history):
            item = rumps.MenuItem(
                _shorten(entry["question"]),
                callback=_in_background(lambda i=index: client.call("POST", "/api/replay", {"index": i})),
            )
            self._recent_item.add(item)

    def _refresh_menu(self, _timer):
        """Runs on the main thread, so it is the only place the menu is mutated."""
        with self._lock:
            notifications, self._notifications = self._notifications, []
        for title, message in notifications:
            rumps.notification("Second Brain", title, message)

        state, connected = self._snapshot()
        history = state["history"] if state and connected else []
        key = (connected, history[0]["asked_at"] if history else None, len(history))
        if key != self._rendered_key:
            self._rebuild_menu()
            self._rendered_key = key

        if not connected:
            self.icon = IDLE_ICON
            return

        self.icon = RECORDING_ICON if state["recording"] else IDLE_ICON
        self._record_item.title = "Stop Recording" if state["recording"] else "Start Recording"

        if state["speaking"]:
            self._speaking_item.title = "● Speaking — click to stop"
            self._speaking_item.set_callback(_in_background(lambda: client.call("POST", "/api/stop")))
        else:
            self._speaking_item.title = "Not Speaking"
            self._speaking_item.set_callback(None)

        if state["syncing"]:
            self._sync_item.title = "Syncing…"
            self._sync_item.set_callback(None)
        else:
            self._sync_item.title = f"Sync Now (last: {_ago(state['last_sync_at'])})"
            self._sync_item.set_callback(_in_background(lambda: client.call("POST", "/api/sync")))

    # --- actions --------------------------------------------------------------

    def _open_window(self):
        import webbrowser
        webbrowser.open(client.base_url() + "/")

    def copy_written(self, _sender):
        state, _connected = self._snapshot()
        if not state or not state["history"]:
            return
        latest = state["history"][0]
        pyperclip.copy(latest["written"] or latest["spoken"])
        rumps.notification("Second Brain", "Copied", "The written answer is on your clipboard.")

    def show_transcript(self, _sender):
        """What it heard and what it said back.

        The menu shows the latest answer, this shows the ones before it, which is
        what you need when an answer is confusing and you can't tell whether it
        misheard the question or just reasoned badly.
        """
        state, _connected = self._snapshot()
        history = state["history"] if state else []

        if history:
            text = TRANSCRIPT_SEPARATOR.join(
                f"[{time.strftime('%d %b %H:%M', time.localtime(entry['asked_at']))}]\n"
                f"heard: {entry['question']}\n\n{entry['written'] or entry['spoken']}"
                for entry in history
            )
        else:
            text = "Nothing asked yet.\n\nUse your shortcut (or Start Recording) and ask a question."

        window = rumps.Window(
            title="Second Brain",
            message="Recent questions, as they were transcribed.",
            default_text=text,
            ok="Close",
            dimensions=(520, 340),
        )
        window.add_button("Copy")

        response = window.run()
        # the extra button lands after ok; the field is editable, so copy what's
        # actually in it rather than what we put there
        if response.clicked == 2:
            pyperclip.copy(response.text)

    def start_service(self, _sender):
        from src.core import autostart

        manager = autostart.backend()
        if manager.installed():
            manager.start()
        else:
            # not set up to run at login -- start one detached from this app, so
            # quitting the menu doesn't take the service with it
            subprocess.Popen(
                [sys.executable, "-m", "src", "serve", "--log-file"],
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )


if __name__ == "__main__":
    SecondBrainApp().run()
