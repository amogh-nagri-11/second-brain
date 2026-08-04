import numpy as np 
import sounddevice as sd 
from scipy.io.wavfile import write 

SAMPLE_RATE = 16000 

class Recorder: 
    def __init__(self): 
        self.recording = [] 
        self.stream = None 

    #start recording => reset buffe, i.e. recording = [] and setup stream using InputStream from sd 
    def start(self):
        self.recording = [] 
        self.stream = sd.InputStream( 
            samplerate=SAMPLE_RATE, 
            channels=1, 
            dtype='int16', 
            callback=self._callback 
        )
        self.stream.start() 

    def _callback(self, indata, frames, time, status): 
        self.recording.append(indata.copy()) 


    def stop(self) -> str:
        self.stream.stop() 
        self.stream.close() 
        audio = np.concatenate(self.recording, axis=0) 
        path = "temp_recording.wav" 
        write(path, SAMPLE_RATE, audio) 
        return path 

    
    