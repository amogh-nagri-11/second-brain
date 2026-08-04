from faster_whisper import WhisperModel 
# faster_whisper is a reimplementation of openai-whisper but at a smaller scale 
# not enough if aiming for a higher accuracy or continuous streams 

class Transcriber: 
    def __init__(self, model_size: str = 'base'): 
        self.model = WhisperModel(model_size, device='cpu', compute_type='int8')

    def transcribe(self, audio_path: str) -> str: 
        segments, _ = self.model.transcribe(audio_path) 
        return " ".join(segment.text for segment in segments).strip() 


    