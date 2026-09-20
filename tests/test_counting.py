"""Exact counts from the store, and the call that decides what to count."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from src.storage import db
from src.storage.types import ActivityRecord
from src.synthesis import answer, tools

NOW = datetime.now(timezone.utc)


def pr(number, ownership="external", state="merged", repo="peft", days_ago=3):
    return ActivityRecord(
        id=f"github:pr:x/{repo}#{number}",
        source="github",
        kind="pr",
        timestamp=(NOW - timedelta(days=days_ago)).isoformat(),
        title=f"{repo} #{number} ({state}): something",
        body="",
        fields={"repo": repo, "state": state, "ownership": ownership, "merged": state == "merged"},
    )


def chunks(record):
    return [(record.title, np.ones(384, dtype=np.float32).tolist())]


class TallyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.get_connection(Path(self._tmp.name) / "brain.db")
        self.addCleanup(self.conn.close)
        for record in [
            pr(1, "external", "merged"),
            pr(2, "external", "merged"),
            pr(3, "external", "open"),
            pr(4, "own", "merged", repo="second-brain"),
            pr(5, "external", "closed", days_ago=400),
        ]:
            db.save_item(self.conn, record, chunks(record))

    def test_counts_everything_matching(self):
        self.assertEqual(db.tally(self.conn, {"kind": "pr"})["total"], 5)

    def test_filters_combine(self):
        result = db.tally(self.conn, {"kind": "pr", "ownership": "external", "state": "merged"})
        self.assertEqual(result["total"], 2)

    def test_groups_break_the_total_down(self):
        groups = db.tally(self.conn, {"kind": "pr"}, group_by="state")["groups"]
        self.assertEqual(groups, {"merged": 3, "open": 1, "closed": 1})

    def test_repo_is_matched_whatever_its_case(self):
        self.assertEqual(db.tally(self.conn, {"repo": "SECOND-BRAIN"})["total"], 1)

    def test_dates_bound_the_count(self):
        recent = (NOW - timedelta(days=30)).isoformat()
        self.assertEqual(db.tally(self.conn, {"kind": "pr"}, since=recent)["total"], 4)

    def test_a_date_bound_covers_the_whole_day(self):
        # the model asks "until <today>", and items stored with a time of day have
        # to fall inside that, not be cut off at midnight
        today = NOW.date().isoformat()
        recent = pr(9, days_ago=0)
        db.save_item(self.conn, recent, chunks(recent))
        self.assertEqual(db.tally(self.conn, {"kind": "pr"}, until=today)["total"], 6)

    def test_deleted_items_are_not_counted(self):
        db.mark_deleted(self.conn, ["github:pr:x/peft#3"])
        self.assertEqual(db.tally(self.conn, {"kind": "pr"})["total"], 4)

    def test_a_long_list_says_it_is_truncated(self):
        result = db.tally(self.conn, {"kind": "pr"}, limit=2)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["listed"], 2)
        self.assertTrue(result["truncated"])

    def test_unknown_filters_are_ignored_rather_than_run(self):
        # the model picks these values, so anything not on the whitelist must not
        # reach the query
        result = db.tally(self.conn, {"nonsense": "1 OR 1=1", "kind": "pr"})
        self.assertEqual(result["total"], 5)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.get_connection(Path(self._tmp.name) / "brain.db")
        self.addCleanup(self.conn.close)
        record = pr(1)
        db.save_item(self.conn, record, chunks(record))

    def test_runs_a_count(self):
        result = json.loads(tools.run_tool(self.conn, "count_activity", '{"kind": "pr"}'))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["counted"], {"kind": "pr"})

    def test_unknown_tool_is_refused(self):
        result = json.loads(tools.run_tool(self.conn, "delete_everything", "{}"))
        self.assertIn("error", result)

    def test_bad_arguments_come_back_as_an_error(self):
        self.assertIn("error", json.loads(tools.run_tool(self.conn, "count_activity", "not json")))


def reply(content=None, tool_calls=None):
    message = mock.Mock(content=content, tool_calls=tool_calls)
    message.model_dump.return_value = {"role": "assistant", "content": content}
    return mock.Mock(choices=[mock.Mock(message=message)])


def tool_call(arguments='{"kind": "pr"}', call_id="call_1"):
    call = mock.Mock(id=call_id)
    # name= is taken by Mock itself, so it has to be set after the fact
    call.function = mock.Mock(arguments=arguments)
    call.function.name = "count_activity"
    return call


class PlanThenAnswerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.get_connection(Path(self._tmp.name) / "brain.db")
        self.addCleanup(self.conn.close)
        for number in (1, 2, 3):
            record = pr(number, state="merged" if number < 3 else "open")
            db.save_item(self.conn, record, chunks(record))

    def test_the_count_is_run_and_reaches_the_answering_prompt(self):
        call = tool_call('{"kind": "pr", "group_by": "state"}')
        replies = [reply(tool_calls=[call]), reply(content="SPOKEN: three\nWRITTEN: three")]
        with mock.patch.object(answer.client().chat.completions, "create", side_effect=replies) as create:
            got = answer.synthesize_answer("how many?", [], conn=self.conn)

        self.assertEqual(got.spoken, "three")
        prompt = create.call_args_list[1].kwargs["messages"][0]["content"]
        self.assertIn("Exact counts from the whole store", prompt)
        self.assertIn('"total": 3', prompt)
        self.assertIn('"merged": 2', prompt)

    def test_the_records_are_sent_once(self):
        # the whole point of counting in a call of its own: the deciding call must
        # not carry the records, or one question spends the token budget twice
        call = tool_call()
        replies = [reply(tool_calls=[call]), reply(content="SPOKEN: a\nWRITTEN: b")]
        with mock.patch.object(answer.client().chat.completions, "create", side_effect=replies) as create:
            answer.synthesize_answer("how many?", [{
                "source": "github", "title": "a distinctive title", "body": "", "timestamp": NOW.isoformat(),
            }], conn=self.conn)

        deciding, answering = [c.kwargs["messages"][0]["content"] for c in create.call_args_list]
        self.assertNotIn("a distinctive title", deciding)
        self.assertIn("a distinctive title", answering)

    def test_a_question_needing_no_count_adds_nothing(self):
        replies = [reply(content="NONE", tool_calls=None), reply(content="SPOKEN: a\nWRITTEN: b")]
        with mock.patch.object(answer.client().chat.completions, "create", side_effect=replies) as create:
            answer.synthesize_answer("what did I do yesterday?", [], conn=self.conn)

        self.assertNotIn("Exact counts", create.call_args_list[1].kwargs["messages"][0]["content"])

    def test_without_a_store_it_answers_from_the_records_alone(self):
        with mock.patch.object(
            answer.client().chat.completions, "create", return_value=reply(content="SPOKEN: a\nWRITTEN: b")
        ) as create:
            answer.synthesize_answer("how many?", [], conn=None)
        self.assertEqual(create.call_count, 1)

    def test_only_a_few_counts_are_run(self):
        calls = [tool_call(call_id=f"c{n}") for n in range(6)]
        replies = [reply(tool_calls=calls), reply(content="SPOKEN: a\nWRITTEN: b")]
        with mock.patch.object(answer.client().chat.completions, "create", side_effect=replies) as create:
            answer.synthesize_answer("how many?", [], conn=self.conn)

        prompt = create.call_args_list[1].kwargs["messages"][0]["content"]
        self.assertEqual(prompt.count('"total":'), answer.MAX_COUNTS)


if __name__ == "__main__":
    unittest.main()
