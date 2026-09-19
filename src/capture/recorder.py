import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000


class Recorder:
    def __init__(self):
        self.recording = []
        self.stream = None

    def start(self):
        self.recording = []
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, indata, frames, time, status):
        self.recording.append(indata.copy())

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
