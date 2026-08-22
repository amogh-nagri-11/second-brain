import pyttsx3

from src.output.speech import to_speech


class Speaker:
    def __init__(self, rate: int = 175):
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", rate)

    def speak(self, text: str):
        # the caller keeps the original for the clipboard and notification; only the
        # spoken copy gets rewritten
        self.engine.say(to_speech(text))
        self.engine.runAndWait()
