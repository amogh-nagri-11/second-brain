"""Offline tests: no network, no database, no models.

The test_*.py scripts under src/ are manual checks against live data; these run
anywhere with `python -m unittest discover tests`.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np

# the model client is built from this on first use
os.environ.setdefault("OPENROUTER_API_KEY", "test")

from src.retrieval.search import keyword_overlap_score, recency_score, score_records
from src.synthesis import answer, llm


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
        reply.choices = [mock.Mock(message=mock.Mock(content="SPOKEN: a\nWRITTEN: b", tool_calls=None))]
        with mock.patch.object(llm.client().chat.completions, "create", return_value=reply) as create:
            answer.synthesize_answer("q", [record("r")], now=datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc))
        prompt = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Saturday 19 Sep 2026", prompt)
        self.assertIn("not been merged", prompt)
        self.assertIn("#<number> (<state>)", prompt)



class ProviderTests(unittest.TestCase):
    def setUp(self):
        llm.forget_client()
        self.addCleanup(llm.forget_client)

    def test_openrouter_by_default(self):
        with mock.patch.object(llm, "setting", side_effect=lambda name, default: default):
            self.assertEqual(llm.base_url(), "https://openrouter.ai/api/v1")
            self.assertEqual(llm.model(), "openai/gpt-oss-120b")

    def test_settings_move_the_model_and_the_provider(self):
        chosen = {"llm_model": "deepseek/deepseek-v4-flash", "llm_base_url": "https://api.groq.com/openai/v1"}
        with mock.patch.object(llm, "setting", side_effect=lambda name, default: chosen.get(name, default)):
            self.assertEqual(llm.model(), "deepseek/deepseek-v4-flash")
            self.assertEqual(llm.client().base_url.host, "api.groq.com")

    def test_the_model_is_not_hard_coded_in_the_callers(self):
        # it used to be a default argument in two unrelated files, which is how a
        # model change turned into a code change
        with mock.patch.object(llm, "setting", side_effect=lambda name, default: "some/model"):
            reply = mock.Mock(choices=[mock.Mock(message=mock.Mock(
                content="SPOKEN: a\nWRITTEN: b", tool_calls=None))])
            with mock.patch.object(llm.client().chat.completions, "create", return_value=reply) as create:
                answer.synthesize_answer("q", [record("r")])
            self.assertEqual(create.call_args.kwargs["model"], "some/model")

    def test_a_missing_key_is_raised_when_asked_for_not_at_import(self):
        from src.config.env import MissingCredential

        with mock.patch.object(llm, "llm_api_key", side_effect=MissingCredential("OPENROUTER_API_KEY isn't set")):
            with self.assertRaises(MissingCredential):
                llm.client()
if __name__ == "__main__":
    unittest.main()
