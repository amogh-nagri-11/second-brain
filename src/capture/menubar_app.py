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

class SecondBrainApp(rumps.App): 
    def __init__(self): 
        super().__init__("", icon = 'icons/idle.png', quit_button="Quit", template=True) 
        self.recorder = Recorder() 
        self.transcriber = Transcriber() 
        self.speaker = Speaker()
        self.is_Recording = False
        self.menu = ["Start/Stop Recording", "Sync Now", "Stop Speaking"]
        atexit.register(self.speaker.stop)

        #listener for keyboard shortcut
        listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        listener.start()

        # one sync at a time, whether triggered by the timer or the menu
        self._sync_lock = threading.Lock()
        threading.Thread(target=self._sync_loop, daemon=True).start()

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

    @rumps.clicked("Start/Stop Recording") 
    def toggle_recording(self, sender): 
        if not self.is_Recording: 
            self._start_recording() 
            sender.title = "Stop Recording"
        else: 
            self._stop_recording()
            sender.title = "Start/Stop Recording" 

    @rumps.clicked("Stop Speaking")
    def stop_speaking(self, sender):
        self.speaker.stop()

    @rumps.clicked("Sync Now")
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

        try:
            result = run_sync()
            if result["changed"]:
                invalidate_cluster_cache()
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
            self._sync_lock.release()

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

