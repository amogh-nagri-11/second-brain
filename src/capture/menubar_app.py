import atexit
import rumps
import threading
import time
import pyperclip
from src.capture.recorder import Recorder
from src.capture.transcriber import Transcriber
from src.output.speaker import Speaker
from src.pipeline import get_answer, invalidate_cluster_cache
from src.sync import run_sync

from pynput import keyboard

HOTKEY = keyboard.Key.f9
SYNC_INTERVAL_SECONDS = 30 * 60
# how many past questions stay in the Recent submenu
MAX_HISTORY = 8
# the menu is rebuilt on a timer rather than from the worker threads, so AppKit is
# only ever touched from the main thread
MENU_REFRESH_SECONDS = 1
RECENT_TITLE_CHARS = 45
TRANSCRIPT_SEPARATOR = "\n\n" + "-" * 52 + "\n\n"


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


class SecondBrainApp(rumps.App): 
    def __init__(self): 
        super().__init__("", icon = 'icons/idle.png', quit_button="Quit", template=True) 
        self.recorder = Recorder() 
        self.transcriber = Transcriber() 
        self.speaker = Speaker()
        self.is_Recording = False

        # newest first: (asked_at, question, answer)
        self._history: list[tuple[float, str, str]] = []
        self._history_version = 0
        self._rendered_history_version = -1
        self._state_lock = threading.Lock()
        self._last_sync_at: float | None = None
        self._syncing = False

        self._build_menu()
        atexit.register(self.speaker.stop)

        #listener for keyboard shortcut
        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        listener.start()

        # one sync at a time, whether triggered by the timer or the menu
        self._sync_lock = threading.Lock()
        threading.Thread(target=self._sync_loop, daemon=True).start()

        rumps.Timer(self._refresh_menu, MENU_REFRESH_SECONDS).start()

    def _build_menu(self):
        self._record_item = rumps.MenuItem("Start Recording", callback=self.toggle_recording)
        # greyed out until there is something to stop
        self._speaking_item = rumps.MenuItem("Not Speaking")
        self._recent_item = rumps.MenuItem("Recent")
        self._transcript_item = rumps.MenuItem("Show Transcript…", callback=self.show_transcript)
        self._sync_item = rumps.MenuItem("Sync Now", callback=self.sync_now)

        self.menu = [
            self._record_item,
            self._speaking_item,
            rumps.separator,
            self._recent_item,
            self._transcript_item,
            rumps.separator,
            self._sync_item,
        ]
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

        # rebuilding a submenu is cheap but not free; only do it when it changed
        if version != self._rendered_history_version:
            self._render_recent()
            self._rendered_history_version = version

    def _render_recent(self):
        # the submenu's NSMenu is created lazily on first add(), and clear() blows up
        # on the None it starts as -- so only clear once something is actually in it
        if len(self._recent_item):
            self._recent_item.clear()

        with self._state_lock:
            history = list(self._history)

        if not history:
            empty = rumps.MenuItem("Nothing asked yet")
            empty.set_callback(None)
            self._recent_item.add(empty)
            return

        for index, (_asked_at, question, _answer) in enumerate(history):
            item = rumps.MenuItem(
                _shorten(question),
                callback=lambda sender, i=index: self._replay(i),
            )
            self._recent_item.add(item)

    def _replay(self, index: int):
        """Re-speak an earlier answer and put it back on the clipboard."""
        with self._state_lock:
            if index >= len(self._history):
                return
            _asked_at, question, answer = self._history[index]

        pyperclip.copy(answer)
        rumps.notification("Second Brain", question, answer)
        self.speaker.speak(answer)

    def show_transcript(self, sender):
        """What it heard and what it said back.

        The menu can show a question but not the transcription next to the answer it
        produced, which is what you need when the answer is confusing and you can't
        tell whether it misheard you or just reasoned badly.
        """
        with self._state_lock:
            history = list(self._history)

        if history:
            text = TRANSCRIPT_SEPARATOR.join(
                f"[{time.strftime('%d %b %H:%M', time.localtime(asked_at))}]\n"
                f"heard: {question}\n\n{answer}"
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

    def _remember(self, question: str, answer: str):
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
            audio_path = self.recorder.stop() 
            threading.Thread(target=self._process, args=(audio_path,)).start() 

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

    def _process(self, audio_path: str):
        query_text = self.transcriber.transcribe(audio_path)
        print(f"You asked: {query_text}")

        answer = get_answer(query_text) 
        print(f"Answer: {answer}") 

        pyperclip.copy(answer)
        rumps.notification("Second Brain", query_text, answer) 
        self._remember(query_text, answer)
        self.speaker.speak(answer)

if __name__ == "__main__":
    SecondBrainApp().run()
