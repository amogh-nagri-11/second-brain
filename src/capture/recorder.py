import threading

import numpy as np

SAMPLE_RATE = 16000

# loudness, as RMS of float samples in -1..1, above which a block counts as speech.
# Quiet rooms sit well under 0.005 and normal speech into a laptop mic is 0.02+
SPEECH_RMS = 0.012
# once something has been said, this much quiet means the question is over
TRAILING_SILENCE_SECONDS = 1.5
# nothing said at all by this point -- give up rather than listen forever
NO_SPEECH_TIMEOUT_SECONDS = 8.0
# a question, not a dictation
MAX_SECONDS = 30.0


class SilenceDetector:
    """Decides when a recording is finished, from the loudness of each block.

    An OS keyboard shortcut only fires when the key goes down, so there is no
    key-up to end a recording on. Pressing again works, but most of the time the
    end of a question is simply the speaker going quiet.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.elapsed = 0.0
        self.heard_speech = False
        self.quiet_for = 0.0

    def feed(self, block: np.ndarray) -> bool:
        """True once the recording should stop."""
        seconds = len(block) / self.sample_rate
        self.elapsed += seconds

        rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
        if rms >= SPEECH_RMS:
            self.heard_speech = True
            self.quiet_for = 0.0
        else:
            self.quiet_for += seconds

        if self.elapsed >= MAX_SECONDS:
            return True
        if self.heard_speech:
            return self.quiet_for >= TRAILING_SILENCE_SECONDS
        return self.elapsed >= NO_SPEECH_TIMEOUT_SECONDS


class Recorder:
    def __init__(self):
        self.recording = []
        self.stream = None
        self._detector: SilenceDetector | None = None
        self._on_silence = None
        self._fired = threading.Event()

    def start(self, on_silence=None):
        """`on_silence` is called once, from the audio thread, when the speaker has
        gone quiet. It must not stop the stream itself -- hand off to another thread."""
        # imported here so the rest of the app, and CI, don't need PortAudio
        import sounddevice as sd

        self.recording = []
        self._detector = SilenceDetector()
        self._on_silence = on_silence
        self._fired.clear()
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, indata, frames, time, status):
        block = indata.copy()
        self.recording.append(block)

        if self._on_silence and not self._fired.is_set() and self._detector.feed(block.reshape(-1)):
            self._fired.set()
            self._on_silence()

    def stop(self) -> np.ndarray:
        """The audio as 16 kHz mono float32, which is what whisper takes directly.

        Handed over in memory rather than through a wav file: a file in the working
        directory fails wherever that isn't writable, and two quick questions would
        race on the same name.
        """
        self.stream.stop()
        self.stream.close()
        if not self.recording:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.recording, axis=0).reshape(-1)
