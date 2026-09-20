"""Following on from the last question -- and knowing when not to."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.conversation import IDLE_RESET_SECONDS, Conversation
from src.core.service import Brain
from src.synthesis import rewrite
from src.synthesis.answer import Answer


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.conversation = Conversation()

    def test_turns_come_back_in_order(self):
        self.conversation.add("first", "one", [], now=100)
        self.conversation.add("second", "two", [], now=110)
        self.assertEqual([t.question for t in self.conversation.recent(now=120)], ["first", "second"])

    def test_only_the_last_few_turns_are_kept(self):
        for n in range(6):
            self.conversation.add(f"q{n}", "a", [], now=100 + n)
        self.assertEqual(len(self.conversation.recent(now=110)), 3)

    def test_a_long_gap_ends_the_conversation(self):
        self.conversation.add("first", "one", ["a"], now=100)
        self.assertEqual(self.conversation.recent(now=100 + IDLE_RESET_SECONDS + 1), [])

    def test_a_question_after_the_gap_starts_clean(self):
        self.conversation.add("first", "one", [], now=100)
        self.conversation.add("second", "two", [], now=100 + IDLE_RESET_SECONDS + 1)
        self.assertEqual([t.question for t in self.conversation.recent(now=100 + IDLE_RESET_SECONDS + 2)], ["second"])

    def test_reset_forgets_everything(self):
        self.conversation.add("first", "one", ["a"], now=100)
        self.conversation.reset()
        self.assertEqual(self.conversation.recent(now=101), [])
        self.assertFalse(self.conversation.active)


class RewriteTests(unittest.TestCase):
    def test_a_question_with_no_conversation_is_left_alone(self):
        with mock.patch.object(rewrite, "standalone_question", wraps=rewrite.standalone_question):
            self.assertEqual(rewrite.standalone_question("how many?", []), "how many?")

    def test_a_follow_up_is_rewritten(self):
        turns = [mock.Mock(question="how many PRs on other projects?", spoken="Thirty-two.")]
        reply = mock.Mock(choices=[mock.Mock(message=mock.Mock(
            content="how many of my pull requests on other people's projects are merged?"))])
        with mock.patch("src.synthesis.llm.client") as client:
            client().chat.completions.create.return_value = reply
            got = rewrite.standalone_question("how many of those merged?", turns)
        self.assertEqual(got, "how many of my pull requests on other people's projects are merged?")

    def test_a_failed_rewrite_falls_back_to_the_question(self):
        turns = [mock.Mock(question="q", spoken="a")]
        with mock.patch("src.synthesis.llm.client") as client:
            client().chat.completions.create.side_effect = RuntimeError("the model provider is down")
            self.assertEqual(rewrite.standalone_question("how many of those?", turns), "how many of those?")

    def test_a_rambling_rewrite_is_not_trusted(self):
        turns = [mock.Mock(question="q", spoken="a")]
        reply = mock.Mock(choices=[mock.Mock(message=mock.Mock(content="here is the answer: " + "x" * 500))])
        with mock.patch("src.synthesis.llm.client") as client:
            client().chat.completions.create.return_value = reply
            self.assertEqual(rewrite.standalone_question("how many of those?", turns), "how many of those?")


class BrainConversationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.dict("os.environ", {"SECOND_BRAIN_HOME": self._tmp.name})
        patch.start()
        self.addCleanup(patch.stop)

        self.asked = []

        def ask(question, turns=None):
            self.asked.append({"question": question, "turns": turns})
            return Answer(spoken=f"spoken:{question}", written="w", context_ids=[f"id:{question}"])

        self.brain = Brain(ask=ask, speaker=mock.Mock(is_speaking=False), run_sync=lambda: {}, copy=lambda text: None)

    def test_the_second_question_carries_the_first(self):
        self.brain.ask("how many PRs?")
        self.brain.ask("how many of those merged?")

        second = self.asked[1]
        self.assertEqual([t.question for t in second["turns"]], ["how many PRs?"])

    def test_the_first_question_carries_nothing(self):
        self.brain.ask("how many PRs?")
        self.assertEqual(self.asked[0]["turns"], [])

    def test_a_new_topic_drops_what_came_before(self):
        self.brain.ask("how many PRs?")
        self.brain.new_topic()
        self.brain.ask("what did I do yesterday?")
        self.assertEqual(self.asked[1]["turns"], [])

    def test_asking_for_a_new_topic_in_the_same_breath(self):
        self.brain.ask("how many PRs?")
        self.brain.ask("what did I do yesterday?", new_topic=True)
        self.assertEqual(self.asked[1]["turns"], [])

    def test_clients_are_told_a_conversation_is_going(self):
        self.assertFalse(self.brain.state()["in_conversation"])
        self.brain.ask("how many PRs?")
        self.assertTrue(self.brain.state()["in_conversation"])
        self.brain.new_topic()
        self.assertFalse(self.brain.state()["in_conversation"])

    def test_the_answer_says_whether_it_followed_on(self):
        self.assertFalse(self.brain.ask("first")["follow_up"])
        self.assertTrue(self.brain.ask("second")["follow_up"])


class RateLimitTests(unittest.TestCase):
    def test_a_rate_limit_is_answered_not_raised(self):
        from openai import RateLimitError

        import src.pipeline as pipeline

        # shaped like a real 429, including a provider that says when to come back
        error = RateLimitError(
            "Error code: 429 - Rate limit reached on tokens per day (TPD): Limit 200000."
            " Please try again in 8m8.159999999s.",
            response=mock.Mock(status_code=429, headers={}),
            body=None,
        )
        with mock.patch.object(pipeline, "get_clusters", return_value=[[{"id": "a"}]]), \
             mock.patch.object(pipeline, "retrieve", return_value=([{"id": "a"}], [])), \
             mock.patch.object(pipeline, "standalone_question", side_effect=lambda q, turns: q), \
             mock.patch.object(pipeline, "synthesize_answer", side_effect=error):
            answer = pipeline.get_answer("how many?")

        self.assertIn("rate limit", answer.spoken.lower())
        self.assertIn("8m8.159999999s", answer.spoken)


if __name__ == "__main__":
    unittest.main()
