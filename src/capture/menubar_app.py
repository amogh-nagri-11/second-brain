import atexit
import re
import rumps
import webbrowser
import textwrap
import threading
import time
import pyperclip
from src.capture.recorder import SAMPLE_RATE, Recorder
from src.capture.transcriber import Transcriber
from src.output.speaker import Speaker
from src.pipeline import get_answer, invalidate_cluster_cache
from src.synthesis.answer import Answer
from src.sync import run_sync
from src.ui.server import UIServer

from pynput import keyboard

HOTKEY = keyboard.Key.f9
# the sources move a few times a day at most, so half-hourly polling was buying
# nothing for 48 network wake-ups a day
SYNC_INTERVAL_SECONDS = 60 * 60
# how many past questions stay in the Recent submenu
MAX_HISTORY = 8
# the menu is rebuilt on a timer rather than from the worker threads, so AppKit is
# only ever touched from the main thread
MENU_REFRESH_SECONDS = 1
RECENT_TITLE_CHARS = 45
# anything shorter is a key tap, not a question
MIN_QUESTION_SAMPLES = SAMPLE_RATE // 2
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


class SecondBrainApp(rumps.App): 
    def __init__(self): 
        super().__init__("", icon = 'icons/idle.png', quit_button="Quit", template=True) 
        self.recorder = Recorder() 
        self.transcriber = Transcriber() 
        self.speaker = Speaker()
        self.is_Recording = False

        # newest first: (asked_at, question, Answer)
        self._history: list[tuple[float, str, Answer]] = []
        self._history_version = 0
        self._rendered_history_version = -1
        self._state_lock = threading.Lock()
        self._last_sync_at: float | None = None
        self._syncing = False

        self.ui = UIServer(self)
        self.ui.start()

        self._build_items()
        self._owns_quit_button = False
        self._rebuild_menu()
        # from here on the menu is ours to rebuild, quit button included
        self._owns_quit_button = True
        atexit.register(self.speaker.stop)

        #listener for keyboard shortcut
        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        listener.start()

        # one sync at a time, whether triggered by the timer or the menu
        self._sync_lock = threading.Lock()
        threading.Thread(target=self._sync_loop, daemon=True).start()

        rumps.Timer(self._refresh_menu, MENU_REFRESH_SECONDS).start()

    def _build_items(self):
        """The long-lived items. These are re-added on every rebuild rather than
        recreated, so the references and callbacks here stay valid."""
        self._window_item = rumps.MenuItem("Open Window", callback=self.open_window)
        self._record_item = rumps.MenuItem("Start Recording", callback=self.toggle_recording)
        # greyed out until there is something to stop
        self._speaking_item = rumps.MenuItem("Not Speaking")
        self._copy_item = rumps.MenuItem("Copy for Docs", callback=self.copy_written)
        self._recent_item = rumps.MenuItem("Recent")
        self._transcript_item = rumps.MenuItem("Show Transcript…", callback=self.show_transcript)
        self._sync_item = rumps.MenuItem("Sync Now", callback=self.sync_now)

    def _rebuild_menu(self):
        """Rebuild the whole dropdown, latest answer included.

        Keys are explicit because two wrapped lines can read identically and the menu
        is a dict keyed by title -- a duplicate would silently drop the second one.
        Only called when the answer actually changed; the per-second refresh just
        retitles the few items that move.
        """
        with self._state_lock:
            history = list(self._history)

        self.menu.clear()

        self.menu["window"] = self._window_item
        self.menu["record"] = self._record_item
        self.menu["speaking"] = self._speaking_item
        self.menu["sep-answer"] = rumps.separator

        if history:
            _asked_at, question, answer = history[0]
            self.menu["heard"] = _disabled(f"heard: {_shorten(question, ANSWER_WIDTH)}")
            self.menu["sep-heard"] = rumps.separator
            for index, line in enumerate(_answer_lines(answer.written or answer.spoken)):
                self.menu[f"ans-{index}"] = _disabled(line)
            self.menu["copy"] = self._copy_item
        else:
            self.menu["heard"] = _disabled("Nothing asked yet — hold F9 to ask")

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

        self._render_recent()

    def _refresh_menu(self, _timer):
        """Runs on the main thread, so it is the only place the menu is mutated."""
        self._record_item.title = "Stop Recording" if self.is_Recording else "Start Recording"

        if self.speaker.is_speaking:
            self._speaking_item.title = "● Speaking — click to stop"
            self._speaking_item.set_callback(self.stop_speaking)
        else:
            self._speaking_item.title = "Not Speaking"
            self._speaking_item.set_callback(None)

        with self._state_lock:
            syncing, last_sync, version = self._syncing, self._last_sync_at, self._history_version

        self._sync_item.title = "Syncing…" if syncing else f"Sync Now (last: {_ago(last_sync)})"
        self._sync_item.set_callback(None if syncing else self.sync_now)

        if version != self._rendered_history_version:
            self._rebuild_menu()
            self._rendered_history_version = version

    def _render_recent(self):
        # the submenu's NSMenu is created lazily on first add(), and clear() blows up
        # on the None it starts as -- so only clear once something is actually in it
        if len(self._recent_item):
            self._recent_item.clear()

        with self._state_lock:
            history = list(self._history)

        if not history:
            self._recent_item.add(_disabled("Nothing asked yet"))
            return

        for index, (_asked_at, question, _answer) in enumerate(history):
            item = rumps.MenuItem(
                _shorten(question),
                callback=lambda sender, i=index: self._replay(i),
            )
            self._recent_item.add(item)

    def _latest(self) -> tuple[float, str, Answer] | None:
        with self._state_lock:
            return self._history[0] if self._history else None

    def copy_written(self, sender):
        latest = self._latest()
        if latest is None:
            return
        _asked_at, _question, answer = latest
        pyperclip.copy(answer.written or answer.spoken)
        rumps.notification("Second Brain", "Copied", "The written answer is on your clipboard.")

    def _replay(self, index: int):
        """Re-speak an earlier answer and put it back on the clipboard."""
        with self._state_lock:
            if index >= len(self._history):
                return
            _asked_at, question, answer = self._history[index]

        pyperclip.copy(answer.written or answer.spoken)
        rumps.notification("Second Brain", question, answer.spoken)
        self.speaker.speak(answer.spoken)

    def show_transcript(self, sender):
        """What it heard and what it said back.

        The menu shows the latest answer, this shows the ones before it, which is
        what you need when an answer is confusing and you can't tell whether it
        misheard the question or just reasoned badly.
        """
        with self._state_lock:
            history = list(self._history)

        if history:
            text = TRANSCRIPT_SEPARATOR.join(
                f"[{time.strftime('%d %b %H:%M', time.localtime(asked_at))}]\n"
                f"heard: {question}\n\n{answer.written or answer.spoken}"
                for asked_at, question, answer in history
            )
        else:
            text = "Nothing asked yet.\n\nHold F9 (or use Start Recording) and ask a question."

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

    def open_window(self, sender):
        webbrowser.open(self.ui.url)

    def ui_state(self) -> dict:
        """Everything the page polls for, in one round trip."""
        with self._state_lock:
            history = list(self._history)
            syncing, last_sync = self._syncing, self._last_sync_at

        return {
            "speaking": self.speaker.is_speaking,
            "recording": self.is_Recording,
            "syncing": syncing,
            "last_sync": _ago(last_sync),
            "history": [
                {
                    "asked_at": asked_at,
                    "at": time.strftime("%H:%M", time.localtime(asked_at)),
                    "question": question,
                    "spoken": answer.spoken,
                    "written": answer.written,
                }
                for asked_at, question, answer in history
            ],
        }

    def ask_text(self, question: str) -> dict:
        """A typed question takes the same path as a spoken one, minus the mic.

        Nothing is spoken: you typed because you were somewhere you couldn't talk,
        and the page is showing the answer anyway. The notification and the
        clipboard grab are skipped for the same reason -- the window already has
        the text and a copy button.
        """
        entry = self._answer_question(question, speak=False, announce=False)
        return {
            "asked_at": entry[0],
            "at": time.strftime("%H:%M", time.localtime(entry[0])),
            "question": entry[1],
            "spoken": entry[2].spoken,
            "written": entry[2].written,
        }

    def replay_index(self, index: int):
        self._replay(index)

    def _answer_question(
        self, query_text: str, speak: bool = True, announce: bool = True
    ) -> tuple[float, str, Answer]:
        answer = get_answer(query_text)
        print(f"Answer: {answer.spoken}")

        if announce:
            # the clipboard gets the written form -- that's the one you paste somewhere
            pyperclip.copy(answer.written or answer.spoken)
            rumps.notification("Second Brain", query_text, answer.spoken)

        self._remember(query_text, answer)

        if speak:
            self.speaker.speak(answer.spoken)

        with self._state_lock:
            return self._history[0]

    def _remember(self, question: str, answer: Answer):
        with self._state_lock:
            self._history.insert(0, (time.time(), question, answer))
            del self._history[MAX_HISTORY:]
            self._history_version += 1

    def _start_recording(self): 
        if not self.is_Recording: 
            # asking the next question is itself a request to stop hearing the last answer
            self.speaker.stop()
            self.recorder.start() 
            self.is_Recording = True 
            self.icon = "icons/recording.png" 

    def _stop_recording(self): 
        if self.is_Recording: 
            self.is_Recording = False 
            self.icon = "icons/idle.png" 
            audio = self.recorder.stop()
            threading.Thread(target=self._process, args=(audio,)).start()

    def _on_press(self, key): 
        if key==HOTKEY and not self.is_Recording: 
            self._start_recording() 

    def _on_release(self, key): 
        if key==HOTKEY and self.is_Recording: 
            self._stop_recording()

    def toggle_recording(self, sender): 
        if not self.is_Recording: 
            self._start_recording() 
        else: 
            self._stop_recording()

    def stop_speaking(self, sender):
        self.speaker.stop()

    def sync_now(self, sender):
        threading.Thread(target=self._sync, args=(True,), daemon=True).start()

    def _sync_loop(self):
        # syncs once at launch, then on an interval, so the DB stays current
        # without anyone remembering to run the ingest script
        while True:
            self._sync(notify=False)
            time.sleep(SYNC_INTERVAL_SECONDS)

    def _sync(self, notify: bool):
        if not self._sync_lock.acquire(blocking=False):
            return

        with self._state_lock:
            self._syncing = True

        try:
            result = run_sync()
            if result["changed"]:
                invalidate_cluster_cache()
            with self._state_lock:
                self._last_sync_at = time.time()
            if notify:
                rumps.notification(
                    "Second Brain",
                    "Sync complete",
                    f"{result['added']} new, {result['updated']} updated",
                )
        except Exception as error:
            print(f"Sync failed: {error}")
            if notify:
                rumps.notification("Second Brain", "Sync failed", str(error))
        finally:
            with self._state_lock:
                self._syncing = False
            self._sync_lock.release()

    def _process(self, audio):
        # a tap of the hotkey rather than a hold -- nothing worth transcribing, and
        # whisper tends to hallucinate a phrase out of near-silence
        if len(audio) < MIN_QUESTION_SAMPLES:
            return
        query_text = self.transcriber.transcribe(audio)
        if not query_text:
            return
        print(f"You asked: {query_text}")
        self._answer_question(query_text)

if __name__ == "__main__":
    SecondBrainApp().run()
