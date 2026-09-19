"""Offline tests: no network, no database, no models.

The test_*.py scripts under src/ are manual checks against live data; these run
anywhere with `python -m unittest discover tests`.
"""

import http.client
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np

# answer.py builds its client at import time from these
os.environ.setdefault("GITHUB_TOKEN", "test")
os.environ.setdefault("GROQ_API_KEY", "test")

from src.retrieval.search import keyword_overlap_score, recency_score, score_records
from src.synthesis import answer
from src.ui.server import UIServer


def record(title, body="", days_ago=0.0, embedding=(1.0, 0.0)):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": title,
        "source": "github",
        "title": title,
        "body": body,
        "timestamp": moment.isoformat(),
        "embedding": np.array(embedding, dtype=np.float32),
    }


class KeywordTests(unittest.TestCase):
    def test_punctuation_does_not_hide_a_match(self):
        self.assertEqual(keyword_overlap_score("what about the parser?", [record("fix (parser)")]), 1.0)

    def test_stopwords_do_not_count(self):
        self.assertEqual(keyword_overlap_score("what did I do on the", [record("what I did on the")]), 0.0)

    def test_hyphenated_names_stay_whole(self):
        self.assertEqual(keyword_overlap_score("second-brain", [record("second-brain: tidy")]), 1.0)


class RankingTests(unittest.TestCase):
    def test_newer_wins_a_tie(self):
        old, new = record("old", days_ago=200), record("new", days_ago=1)
        ranked = score_records(np.array([1.0, 0.0]), "commit", [old, new])
        self.assertEqual(ranked[0][0]["id"], "new")

    def test_recency_halves_at_half_life(self):
        self.assertAlmostEqual(recency_score([record("x", days_ago=45)], datetime.now(timezone.utc)), 0.5, places=3)


class AnswerTests(unittest.TestCase):
    def test_split_on_markers(self):
        got = answer._split("SPOKEN:\nTwo commits.\nWRITTEN:\n- one\n- two")
        self.assertEqual((got.spoken, got.written), ("Two commits.", "- one\n- two"))

    def test_split_without_markers_fills_both(self):
        got = answer._split("just text")
        self.assertEqual((got.spoken, got.written), ("just text", "just text"))

    def test_prompt_carries_today_and_branch_convention(self):
        reply = mock.Mock()
        reply.choices = [mock.Mock(message=mock.Mock(content="SPOKEN: a\nWRITTEN: b"))]
        with mock.patch.object(answer.client.chat.completions, "create", return_value=reply) as create:
            answer.synthesize_answer("q", [record("r")], now=datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc))
        prompt = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Saturday 19 Sep 2026", prompt)
        self.assertIn("not been merged", prompt)


class ServerGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = mock.Mock()
        cls.app.ui_state.return_value = {"ok": True}
        cls.server = UIServer(cls.app, port=0)
        # port 0 picks a free one; start() needs to know it for the host check
        from http.server import ThreadingHTTPServer
        probe = ThreadingHTTPServer(("127.0.0.1", 0), None)
        cls.server.port = probe.server_address[1]
        probe.server_close()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port)
        conn.request(method, path, body=body, headers=headers or {})
        status = conn.getresponse().status
        conn.close()
        return status

    def test_own_page_can_read_state(self):
        self.assertEqual(self.request("GET", "/api/state"), 200)

    def test_rebound_host_cannot_read_state(self):
        self.assertEqual(self.request("GET", "/api/state", {"Host": f"evil.example:{self.server.port}"}), 403)

    def test_cross_site_form_post_is_refused(self):
        self.app._start_recording.reset_mock()
        status = self.request(
            "POST", "/api/record",
            {"Content-Type": "text/plain", "Origin": "https://evil.example"},
            json.dumps({"recording": True}),
        )
        self.assertEqual(status, 403)
        self.app._start_recording.assert_not_called()

    def test_foreign_origin_is_refused_even_as_json(self):
        status = self.request(
            "POST", "/api/stop",
            {"Content-Type": "application/json", "Origin": "https://evil.example"}, "{}",
        )
        self.assertEqual(status, 403)

    def test_own_page_can_post(self):
        status = self.request(
            "POST", "/api/stop",
            {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.server.port}"}, "{}",
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
