import threading

from faster_whisper import WhisperModel 
# faster_whisper is a reimplementation of openai-whisper but at a smaller scale 
# not enough if aiming for a higher accuracy or continuous streams 

class Transcriber: 
    def __init__(self, model_size: str = 'base'): 
        # loading this costs ~540MB resident, and the app sits idle most of the day,
        # so it waits until something is actually transcribed
        self._model_size = model_size
        self._model: WhisperModel | None = None
        self._load_lock = threading.Lock()

    @property
    def model(self) -> WhisperModel:
        with self._load_lock:
            if self._model is None:
                self._model = WhisperModel(self._model_size, device='cpu', compute_type='int8')
            return self._model

    def transcribe(self, audio_path: str) -> str: 
        segments, _ = self.model.transcribe(audio_path) 
        return " ".join(segment.text for segment in segments).strip() 
