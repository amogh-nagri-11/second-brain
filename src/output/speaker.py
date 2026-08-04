import pyttsx3 

class Speaker: 
    def __init__(self, rate: int=175): 
        self.engine = pyttsx3.init() 
        self.engine.setProperty("rate", rate) 

    def speak(self, text: str): 
        self.engine.say(text) 
        self.engine.runAndWait() 
        