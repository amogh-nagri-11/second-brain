"""Pull request ingestion, against a recorded search result -- no network."""

import unittest
from datetime import datetime, timezone
from unittest import mock

from src.ingestion import github_prs

ME = "amogh-nagri-11"


def raw(
    number=42,
    repo="amogh-nagri-11/second-brain",
    title="Add a PR source",
    state="open",
    merged_at=None,
    closed_at=None,
    draft=False,
    labels=(),
    body="why this exists",
):
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": state,
        "draft": draft,
        "labels": [{"name": name} for name in labels],
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-07T17:33:03Z",
        "closed_at": closed_at,
        "repository_url": f"https://api.github.com/repos/{repo}",
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "pull_request": {"merged_at": merged_at},
    }


class RecordTests(unittest.TestCase):
    def test_own_repo_pr(self):
        record = github_prs.pr_record(raw(), ME)
        self.assertEqual(record.id, "github:pr:amogh-nagri-11/second-brain#42")
        self.assertEqual(record.kind, "pr")
        self.assertEqual(record.fields["ownership"], "own")
        self.assertEqual(record.fields["state"], "open")
        self.assertFalse(record.fields["merged"])
        self.assertEqual(record.title, "second-brain #42 (open): Add a PR source")

    def test_external_pr_is_named_in_full(self):
        record = github_prs.pr_record(raw(repo="huggingface/peft", number=3759), ME)
        self.assertEqual(record.fields["ownership"], "external")
        self.assertIn("huggingface/peft #3759", record.title)
        self.assertIn("external", record.title)

    def test_merged_pr_is_dated_when_it_landed(self):
        record = github_prs.pr_record(
            raw(state="closed", merged_at="2026-09-07T17:33:03Z", closed_at="2026-09-07T17:33:03Z"), ME
        )
        self.assertEqual(record.fields["state"], "merged")
        self.assertTrue(record.fields["merged"])
        self.assertEqual(record.timestamp, "2026-09-07T17:33:03Z")

    def test_closed_without_merging_is_not_merged(self):
        # github's own state says "closed" either way; only the merge time separates
        # an abandoned pull request from a landed one
        record = github_prs.pr_record(raw(state="closed", closed_at="2026-09-07T17:33:03Z"), ME)
        self.assertEqual(record.fields["state"], "closed")
        self.assertFalse(record.fields["merged"])
        self.assertEqual(record.timestamp, "2026-09-01T10:00:00Z")

    def test_draft_and_labels_are_kept(self):
        record = github_prs.pr_record(raw(draft=True, labels=("bug", "ssm")), ME)
        self.assertTrue(record.fields["draft"])
        self.assertEqual(record.fields["labels"], ["bug", "ssm"])
        self.assertIn("draft", record.title)
        self.assertIn("bug, ssm", record.body)

    def test_owner_case_does_not_decide_ownership(self):
        record = github_prs.pr_record(raw(repo="Amogh-Nagri-11/portfolio"), "amogh-nagri-11")
        self.assertEqual(record.fields["ownership"], "own")


class FetchTests(unittest.TestCase):
    def test_searches_by_update_time_and_maps_every_result(self):
        issues = [mock.Mock(raw_data=raw(number=n)) for n in (1, 2)]
        gh = mock.Mock()
        gh.get_user.return_value = mock.Mock(login=ME)
        gh.search_issues.return_value = issues

        with mock.patch.object(github_prs, "Github", return_value=gh), \
             mock.patch.object(github_prs, "github_token", return_value="t"):
            records = github_prs.fetch_recent_prs(datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc))

        query = gh.search_issues.call_args.args[0]
        self.assertEqual(query, f"is:pr author:{ME} updated:>=2026-09-13")
        self.assertEqual([r.fields["number"] for r in records], [1, 2])


class LookbackTests(unittest.TestCase):
    def test_first_pr_sync_reaches_further_back_than_commits(self):
        from src.ingestion.registry import sources

        registry = sources()
        self.assertGreater(
            registry.get("github_prs").first_lookback_days,
            registry.get("github").first_lookback_days,
        )

    def test_later_syncs_use_the_cursor_not_the_lookback(self):
        from src import sync

        with mock.patch.object(sync, "get_last_synced_at", return_value="2026-09-18T00:00:00+00:00"):
            since = sync._since_for(None, "github_prs", github_prs.LOOKBACK_DAYS)
        self.assertEqual(since.year, 2026)
        self.assertEqual(since.month, 9)


if __name__ == "__main__":
    unittest.main()
