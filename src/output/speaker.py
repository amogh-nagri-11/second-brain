"""Speech output.

A subprocess per answer rather than pyttsx3: pyttsx3's run loop ends after the
first runAndWait() and every later call returns instantly without speaking, so
only the first answer of a session was ever audible. A subprocess also gives us
something pyttsx3 does not -- a handle we can kill, so a long answer can be cut
off instead of having to be waited out.

Each OS has a speech engine that fits that shape:
    macOS    say
    Windows  SAPI, through PowerShell's System.Speech
    Linux    espeak-ng (or espeak)
The text goes in on stdin everywhere but macOS, so nothing in it can be read as a
command-line flag or break PowerShell's quoting.
"""

import shutil
import subprocess
import sys
import threading

from src.output.speech import to_speech


# SAPI rates run -10..10 around a default of roughly 175 words a minute
_SAPI_WORDS_PER_STEP = 15
_SAPI_SCRIPT = (
    "[Console]::InputEncoding = [Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$s.Rate = {rate};"
    "$s.Speak([Console]::In.ReadToEnd())"
)


def speech_command(text: str, rate: int, platform: str = sys.platform) -> tuple[list[str], str | None] | None:
    """(argv, stdin text) for this OS, or None when there is no engine to use."""
    if platform == "darwin":
        return ["say", "-r", str(rate), "--", text], None

    if platform == "win32":
        sapi_rate = max(-10, min(10, round((rate - 175) / _SAPI_WORDS_PER_STEP)))
        script = _SAPI_SCRIPT.format(rate=sapi_rate)
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], text

    for engine in ("espeak-ng", "espeak"):
        if shutil.which(engine):
            return [engine, "-s", str(rate), "--stdin"], text
    return None


class Speaker:
    def __init__(self, rate: int = 175):
        self.rate = rate
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._warned = False

    def speak(self, text: str, on_done=None):
        """Start speaking, cutting off whatever is already being said.

        Returns as soon as speech starts so the caller isn't pinned for the length
        of the answer -- that's what makes stop() reachable while it plays.
        `on_done` is called once this utterance ends, finished or cut off.
        """
        # the caller keeps the original for the clipboard and notification; only the
        # spoken copy gets rewritten
        spoken = to_speech(text)
        if not spoken:
            return

        command = speech_command(spoken, self.rate)
        if command is None:
            if not self._warned:
                print("[speaker] no speech engine found (install espeak-ng); answers stay text-only")
                self._warned = True
            return
        argv, stdin_text = command

        with self._lock:
            self._stop_locked()
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                # without this Windows flashes a console window for every answer
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if stdin_text is not None:
                self._proc.stdin.write(stdin_text.encode("utf-8"))
                self._proc.stdin.close()
            proc = self._proc

        if on_done is not None:
            def wait():
                proc.wait()
                on_done()
            threading.Thread(target=wait, daemon=True).start()

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
