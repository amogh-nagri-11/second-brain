"""The app itself, with no UI attached.

Everything that used to live in the menubar class -- asking, recording, speaking,
history, the hourly sync -- lives here, so the same process can sit behind the
local window, the CLI and the menubar on any OS. Clients never touch this object
directly; they go through the API in src.core.api.

Every change of state is published as an event, which is what lets the window and
the menubar update the moment something happens instead of polling for it.
"""

import threading
import time
from typing import Callable

from src.core.conversation import Conversation
from src.storage.db import add_history, get_connection, recent_history

# the source moves a few times a day at most
SYNC_INTERVAL_SECONDS = 60 * 60
# how many past questions the clients are sent
HISTORY_LIMIT = 8
# anything shorter is a key tap, not a question -- and whisper tends to
# hallucinate a phrase out of near-silence
MIN_QUESTION_SECONDS = 0.5


class Brain:
    def __init__(
        self,
        ask: Callable | None = None,
        speaker=None,
        recorder=None,
        transcriber=None,
        run_sync: Callable | None = None,
        copy: Callable[[str], None] | None = None,
    ):
        # the heavy imports -- embeddings, whisper, the audio stack -- only happen in
        # the process that serves, and only the parts actually used
        if ask is None:
            from src.pipeline import get_answer as ask
        if speaker is None:
            from src.output.speaker import Speaker
            speaker = Speaker()
        if run_sync is None:
            from src.sync import run_sync
        if copy is None:
            copy = _copy_to_clipboard

        self._ask = ask
        self.speaker = speaker
        self._recorder = recorder
        self._transcriber = transcriber
        self._run_sync = run_sync
        self._copy = copy

        self._lock = threading.Lock()
        self._recording = False
        self._thinking = False
        self._syncing = False
        self._last_sync_at: float | None = None
        self._last_sync_result: dict | None = None
        self._sync_lock = threading.Lock()

        # what has been asked recently, so a follow-up can lean on it
        self.conversation = Conversation()

        self._subscribers: list[Callable[[dict], None]] = []
        self._subscribers_lock = threading.Lock()

    # --- events ---------------------------------------------------------------

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        """Call `callback(event)` on every change; returns a function to unsubscribe.
        Callbacks run on whichever thread made the change, so they must be quick."""
        with self._subscribers_lock:
            self._subscribers.append(callback)

        def unsubscribe():
            with self._subscribers_lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def _publish(self, kind: str, **extra):
        event = {"type": kind, "state": self.state(), **extra}
        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception as error:
                print(f"[service] subscriber failed: {error}")

    # --- state ----------------------------------------------------------------

    def history(self, limit: int = HISTORY_LIMIT) -> list[dict]:
        conn = get_connection()
        try:
            return recent_history(conn, limit)
        finally:
            conn.close()

    def state(self) -> dict:
        with self._lock:
            state = {
                "recording": self._recording,
                "thinking": self._thinking,
                "syncing": self._syncing,
                "last_sync_at": self._last_sync_at,
                "last_sync": self._last_sync_result,
            }
        state["in_conversation"] = self.conversation.active
        state["speaking"] = self.speaker.is_speaking
        state["history"] = self.history()
        return state

    # --- asking ---------------------------------------------------------------

    def ask(self, question: str, via: str = "typed", new_topic: bool = False) -> dict:
        """Answer a question and remember it.

        A spoken question is answered out loud and its written form goes on the
        clipboard. A typed one is neither: you typed because you couldn't talk,
        and the window already shows the text with a copy button.

        Unless it starts a new topic, the question is answered as the next turn of
        the conversation, so it can refer back to the last one.
        """
        if new_topic:
            self.conversation.reset()
        turns = self.conversation.recent()

        with self._lock:
            self._thinking = True
        self._publish("thinking", question=question)

        try:
            answer = self._ask(question, turns=turns)
        except Exception:
            with self._lock:
                self._thinking = False
            self._publish("thinking")
            raise
        with self._lock:
            self._thinking = False

        asked_at = time.time()
        self.conversation.add(question, answer.spoken, getattr(answer, "context_ids", []), asked_at)

        conn = get_connection()
        try:
            add_history(conn, asked_at, question, answer.spoken, answer.written, via)
        finally:
            conn.close()

        entry = {
            "asked_at": asked_at,
            "question": question,
            "spoken": answer.spoken,
            "written": answer.written,
            "via": via,
            "follow_up": bool(turns),
        }
        print(f"[service] {via}: {question!r} -> {answer.spoken!r}")

        if via == "voice":
            self._copy(answer.written or answer.spoken)
            self._say(answer.spoken)

        self._publish("answer", entry=entry)
        return entry

    def new_topic(self):
        """Forget the conversation so far; the next question starts clean."""
        self.conversation.reset()
        self._publish("conversation")

    def replay(self, index: int):
        """Speak an earlier answer again and put it back on the clipboard."""
        history = self.history()
        if not 0 <= index < len(history):
            return
        entry = history[index]
        self._copy(entry["written"] or entry["spoken"])
        self._say(entry["spoken"])

    def _say(self, text: str):
        self.speaker.speak(text, on_done=lambda: self._publish("speaking"))
        self._publish("speaking")

    def stop_speaking(self):
        self.speaker.stop()
        self._publish("speaking")

    # --- recording ------------------------------------------------------------

    def _audio(self):
        # the speech model is ~540 MB resident, so it and the audio stack load on
        # the first recording rather than at startup
        if self._recorder is None:
            from src.capture.recorder import Recorder
            from src.capture.transcriber import Transcriber
            self._recorder = Recorder()
            self._transcriber = Transcriber()
        return self._recorder, self._transcriber

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    def start_recording(self):
        recorder, _ = self._audio()
        with self._lock:
            if self._recording:
                return
            self._recording = True
        # asking the next question is itself a request to stop hearing the last answer
        self.speaker.stop()
        recorder.start(on_silence=self._stopped_by_silence)
        self._publish("recording")

    def stop_recording(self):
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        recorder, _ = self._audio()
        audio = recorder.stop()
        self._publish("recording")
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def _stopped_by_silence(self):
        # called from the audio thread, which must not be the one to close its own
        # stream
        threading.Thread(target=self.stop_recording, daemon=True).start()

    def _process(self, audio):
        from src.capture.recorder import SAMPLE_RATE

        if len(audio) < MIN_QUESTION_SECONDS * SAMPLE_RATE:
            return
        _, transcriber = self._audio()
        question = transcriber.transcribe(audio)
        if question:
            self.ask(question, via="voice")

    # --- syncing --------------------------------------------------------------

    def start_scheduler(self):
        """Sync once now, then every SYNC_INTERVAL_SECONDS, so the store stays
        current without anyone remembering to run the ingest script."""

        def loop():
            while True:
                self.sync()
                time.sleep(SYNC_INTERVAL_SECONDS)

        threading.Thread(target=loop, daemon=True).start()

    def sync(self, requested: bool = False) -> dict | None:
        """Returns the counts, or None if a sync was already running."""
        if not self._sync_lock.acquire(blocking=False):
            return None

        with self._lock:
            self._syncing = True
        self._publish("syncing")

        result: dict
        try:
            result = self._run_sync()
            if result.get("changed"):
                from src.pipeline import invalidate_cluster_cache
                invalidate_cluster_cache()
        except Exception as error:
            print(f"[service] sync failed: {error}")
            result = {"error": str(error)}
        finally:
            with self._lock:
                self._syncing = False
                self._last_sync_at = time.time()
                self._last_sync_result = result
            self._sync_lock.release()

        # `requested` tells clients this was asked for, so worth a notification
        self._publish("synced", result=result, requested=requested)
        return result


def _copy_to_clipboard(text: str):
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception as error:
        # Linux without xclip / wl-copy has no clipboard to reach
        print(f"[service] clipboard unavailable: {error}")
