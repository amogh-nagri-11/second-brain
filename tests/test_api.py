import json
import os
import signal
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from src.core.api import create_app

PORT = 8765
TOKEN = "secret-token"
OURS = f"http://127.0.0.1:{PORT}"


class FakeBrain:
    def __init__(self):
        self.recording = False
        self.calls = []
        self.subscribers = []

    def state(self):
        return {"recording": self.recording, "history": []}

    def ask(self, question, via, new_topic=False):
        self.calls.append(("ask", question, via))
        return {"question": question, "spoken": "s", "written": "w"}

    def toggle_recording(self):
        self.calls.append(("toggle",))
        self.recording = not self.recording

    def start_recording(self):
        self.calls.append(("start",))
        self.recording = True

    def stop_recording(self):
        self.recording = False

    def stop_speaking(self):
        self.calls.append(("stop",))

    def subscribe(self, callback):
        self.subscribers.append(callback)
        return lambda: None


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.brain = FakeBrain()
        self.client = TestClient(create_app(self.brain, TOKEN, PORT), base_url=OURS)

    def post(self, path, body=None, token=TOKEN, **headers):
        if token:
            headers["X-Second-Brain-Token"] = token
        headers.setdefault("Content-Type", "application/json")
        return self.client.post(path, content=json.dumps(body or {}), headers=headers)

    def test_page_carries_the_token(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(TOKEN, page.text)
        self.assertNotIn("__SECOND_BRAIN_TOKEN__", page.text)

    def test_state_needs_the_token(self):
        self.assertEqual(self.client.get("/api/state").status_code, 401)
        self.assertEqual(self.client.get("/api/state", headers={"X-Second-Brain-Token": "wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/api/state", headers={"X-Second-Brain-Token": TOKEN}).status_code, 200)

    def test_rebound_host_cannot_read_the_page(self):
        # the page holds the token, so a DNS-rebinding site must not get it
        page = self.client.get("/", headers={"Host": f"evil.example:{PORT}"})
        self.assertEqual(page.status_code, 403)
        self.assertNotIn(TOKEN, page.text)

    def test_cross_site_form_post_is_refused(self):
        status = self.post("/api/record", {"recording": True}, token=None,
                           **{"Content-Type": "text/plain", "Origin": "https://evil.example"}).status_code
        self.assertEqual(status, 403)
        self.assertFalse(self.brain.recording)

    def test_foreign_origin_is_refused_even_with_the_token(self):
        self.assertEqual(self.post("/api/stop", Origin="https://evil.example").status_code, 403)

    def test_non_json_is_refused(self):
        self.assertEqual(self.post("/api/stop", **{"Content-Type": "text/plain"}).status_code, 415)

    def test_ask_is_typed(self):
        reply = self.post("/api/ask", {"question": "  what shipped?  "}, Origin=OURS)
        self.assertEqual(reply.json()["written"], "w")
        self.assertEqual(self.brain.calls, [("ask", "what shipped?", "typed")])

    def test_empty_question_is_refused(self):
        self.assertEqual(self.post("/api/ask", {"question": "  "}).status_code, 400)

    def test_record_toggles_with_no_state(self):
        self.assertEqual(self.post("/api/record").json(), {"recording": True})
        self.assertEqual(self.post("/api/record").json(), {"recording": False})
        self.assertEqual(self.post("/api/record", {"recording": True}).json(), {"recording": True})

    def test_events_need_the_token(self):
        self.assertEqual(self.client.get("/api/events").status_code, 401)


class StoppingTests(unittest.TestCase):
    """`stop` used to mean "stop speaking", so a service started by hand had to be
    hunted down with pkill. The pid is in the service file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"SECOND_BRAIN_HOME": self._tmp.name})
        patch.start()
        self.addCleanup(patch.stop)

    def test_nothing_running_is_not_an_error(self):
        from src.__main__ import _stop_service

        self.assertEqual(_stop_service(), 0)

    def test_it_asks_the_recorded_pid_to_stop(self):
        from src.__main__ import _stop_service
        from src.core import client

        client.write_service_file(8765, "token")
        signals = []
        with mock.patch("os.kill", side_effect=lambda pid, sig: signals.append((pid, sig))), \
             mock.patch.object(client, "is_running", return_value=False):
            self.assertEqual(_stop_service(), 0)

        self.assertEqual([sig for _pid, sig in signals], [signal.SIGTERM])

    def test_a_service_that_will_not_stop_is_killed(self):
        from src.__main__ import _stop_service
        from src.core import client

        client.write_service_file(8765, "token")
        signals = []
        with mock.patch("os.kill", side_effect=lambda pid, sig: signals.append(sig)), \
             mock.patch.object(client, "is_running", return_value=True), \
             mock.patch("time.sleep"):
            _stop_service()

        self.assertEqual(signals[-1], signal.SIGKILL)

    def test_a_stale_service_file_is_cleaned_up(self):
        from src.__main__ import _stop_service
        from src.core import client

        client.write_service_file(8765, "token")
        with mock.patch("os.kill", side_effect=ProcessLookupError):
            self.assertEqual(_stop_service(), 0)
        self.assertIsNone(client.read_service_file())


if __name__ == "__main__":
    unittest.main()
