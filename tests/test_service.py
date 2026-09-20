import os
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

from src.capture.recorder import SAMPLE_RATE, SilenceDetector
from src.core.service import Brain
from src.synthesis.answer import Answer


class FakeSpeaker:
    def __init__(self):
        self.said = []
        self.is_speaking = False

    def speak(self, text, on_done=None):
        self.said.append(text)
        if on_done:
            on_done()

    def stop(self):
        pass


class FakeRecorder:
    def __init__(self, audio):
        self.audio = audio
        self.on_silence = None

    def start(self, on_silence=None):
        self.on_silence = on_silence

    def stop(self):
        return self.audio


class FakeTranscriber:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text


def seconds(n, loud=False):
    return np.full(int(n * SAMPLE_RATE), 0.1 if loud else 0.0, dtype=np.float32)


class BrainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        patch = mock.patch.dict(os.environ, {"SECOND_BRAIN_HOME": self._tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._tmp.cleanup)

        self.speaker = FakeSpeaker()
        self.copied = []
        self.events = []

    def brain(self, audio=None, heard="what did I ship?"):
        brain = Brain(
            ask=lambda q, **kwargs: Answer(spoken=f"spoken:{q}", written=f"written:{q}"),
            speaker=self.speaker,
            recorder=FakeRecorder(seconds(2) if audio is None else audio),
            transcriber=FakeTranscriber(heard),
            run_sync=lambda: {"added": 1, "updated": 0, "changed": False},
            copy=self.copied.append,
        )
        brain.subscribe(self.events.append)
        return brain

    def test_typed_question_is_remembered_but_not_spoken(self):
        brain = self.brain()
        brain.ask("hello")
        self.assertEqual(brain.history()[0]["question"], "hello")
        self.assertEqual(brain.history()[0]["via"], "typed")
        self.assertEqual(self.speaker.said, [])
        self.assertEqual(self.copied, [])

    def test_history_survives_a_new_brain(self):
        self.brain().ask("first")
        self.assertEqual(self.brain().history()[0]["question"], "first")

    def test_voice_question_is_spoken_and_copied(self):
        brain = self.brain()
        brain.start_recording()
        brain.stop_recording()
        self._wait_for("answer")
        self.assertEqual(self.speaker.said, ["spoken:what did I ship?"])
        self.assertEqual(self.copied, ["written:what did I ship?"])

    def test_a_tap_is_not_a_question(self):
        brain = self.brain(audio=seconds(0.1))
        brain.start_recording()
        brain.stop_recording()
        brain.toggle_recording()  # starts again: the tap left nothing running
        self.assertTrue(brain.recording)
        self.assertEqual(brain.history(), [])

    def test_silence_stops_the_recording(self):
        brain = self.brain()
        brain.start_recording()
        brain._recorder.on_silence()
        self._wait_for("answer")
        self.assertFalse(brain.recording)

    def test_failed_answer_clears_thinking(self):
        def broken(_q, **kwargs):
            raise RuntimeError("groq down")
        brain = self.brain()
        brain._ask = broken
        with self.assertRaises(RuntimeError):
            brain.ask("q")
        self.assertFalse(brain.state()["thinking"])
        self.assertFalse(self.events[-1]["state"]["thinking"])

    def test_sync_publishes_its_counts(self):
        brain = self.brain()
        brain.sync(requested=True)
        event = self.events[-1]
        self.assertEqual((event["type"], event["result"]["added"], event["requested"]), ("synced", 1, True))
        self.assertIsNotNone(brain.state()["last_sync_at"])

    def test_replay_out_of_range_is_ignored(self):
        self.brain().replay(5)
        self.assertEqual(self.speaker.said, [])

    def _wait_for(self, kind, timeout=2.0):
        done = threading.Event()
        deadline = threading.Timer(timeout, done.set)
        deadline.start()
        while not done.is_set():
            if any(e["type"] == kind for e in self.events):
                deadline.cancel()
                return
            done.wait(0.01)
        self.fail(f"no {kind!r} event")


class SilenceDetectorTests(unittest.TestCase):
    def feed(self, detector, block):
        # the audio callback hands over ~10 ms blocks
        step = SAMPLE_RATE // 100
        return any(detector.feed(block[i:i + step]) for i in range(0, len(block), step))

    def test_stops_after_speech_then_quiet(self):
        d = SilenceDetector()
        self.assertFalse(self.feed(d, seconds(1, loud=True)))
        self.assertFalse(self.feed(d, seconds(1)))
        self.assertTrue(self.feed(d, seconds(0.6)))

    def test_a_pause_mid_question_does_not_stop_it(self):
        d = SilenceDetector()
        self.feed(d, seconds(1, loud=True))
        self.feed(d, seconds(1))
        self.assertFalse(self.feed(d, seconds(0.2, loud=True)))
        self.assertFalse(self.feed(d, seconds(1)))

    def test_gives_up_when_nothing_is_said(self):
        d = SilenceDetector()
        self.assertFalse(self.feed(d, seconds(7)))
        self.assertTrue(self.feed(d, seconds(1.5)))

    def test_caps_a_long_recording(self):
        d = SilenceDetector()
        self.assertTrue(self.feed(d, seconds(31, loud=True)))


if __name__ == "__main__":
    unittest.main()
