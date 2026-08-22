"""Speech output.

Backed by macOS's `say` rather than pyttsx3: pyttsx3's run loop ends after the
first runAndWait() and every later call returns instantly without speaking, so
only the first answer of a session was ever audible. A subprocess also gives us
something pyttsx3 does not -- a handle we can kill, so a long answer can be cut
off instead of having to be waited out.
"""

import subprocess
import threading

from src.output.speech import to_speech


class Speaker:
    def __init__(self, rate: int = 175):
        self.rate = rate
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def speak(self, text: str):
        """Start speaking, cutting off whatever is already being said.

        Returns as soon as speech starts so the caller isn't pinned for the length
        of the answer -- that's what makes stop() reachable while it plays.
        """
        # the caller keeps the original for the clipboard and notification; only the
        # spoken copy gets rewritten
        spoken = to_speech(text)
        if not spoken:
            return

        with self._lock:
            self._stop_locked()
            self._proc = subprocess.Popen(
                ["say", "-r", str(self.rate), "--", spoken],
                stdin=subprocess.DEVNULL,
            )

    def stop(self):
        """Cut speech off mid-sentence. Safe to call when nothing is speaking."""
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def wait(self):
        """Block until the current utterance finishes. Only needed by scripts -- the
        menubar app deliberately doesn't."""
        proc = self._proc
        if proc is not None:
            proc.wait()
