import rumps 
import threading 
import pyperclip
from src.capture.recorder import Recorder 
from src.capture.transcriber import Transcriber
from src.output.speaker import Speaker
from src.pipeline import get_answer

from pynput import keyboard 

HOTKEY = keyboard.Key.f9

class SecondBrainApp(rumps.App): 
    def __init__(self): 
        super().__init__("", icon = 'icons/idle.png', quit_button="Quit", template=True) 
        self.recorder = Recorder() 
        self.transcriber = Transcriber() 
        self.speaker = Speaker()
        self.is_Recording = False 
        self.menu = ["Start/Stop Recording"] 

        #listener for keyboard shortcut 
        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release) 
        listener.start() 

    def _start_recording(self): 
        if not self.is_Recording: 
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

    @rumps.clicked("Start/Stop Recording") 
    def toggle_recording(self, sender): 
        if not self.is_Recording: 
            self._start_recording() 
            sender.title = "Stop Recording"
        else: 
            self._stop_recording()
            sender.title = "Start/Stop Recording" 

    def _process(self, audio_path: str): 
        query_text = self.transcriber.transcribe(audio_path)
        print(f"You asked: {query_text}")

        answer = get_answer(query_text) 
        print(f"Answer: {answer}") 

        pyperclip.copy(answer)
        rumps.notification("Second Brain", query_text, answer) 
        self.speaker.speak(answer)

if __name__ == "__main__":
    SecondBrainApp().run()

