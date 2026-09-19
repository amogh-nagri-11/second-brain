from datetime import datetime

from dateutil import parser as date_parser
from github import Github, GithubException

from src.config.env import github_token
from src.storage.types import ActivityRecord


def _repo_has_activity_since(repo, since: datetime) -> bool:
    """Cheap pre-filter -- pushed_at comes back with the repo listing, so we can skip the
    per-repo commits call for anything untouched in the window."""
    pushed_at = repo.pushed_at
    if pushed_at is None:
        return False
    if pushed_at.tzinfo is None:
        pushed_at = pushed_at.replace(tzinfo=since.tzinfo)
    return pushed_at >= since


# a repo with a very large number of branches would cost a call each; in practice
# personal repos and forks have a handful, and the newest work is not hiding in the
# hundredth one
MAX_BRANCHES_PER_REPO = 40


def _commit_record(repo, commit, branch: str) -> ActivityRecord:
    message = commit.commit.message
    subject = message.split("\n")[0] if message else message

    # branches are walked default-first and deduped by sha, so anything still tagged
    # with a topic branch has not landed yet -- that is the part worth seeing in the
    # title. Commits on the default branch stay unadorned.
    label = repo.name if branch == repo.default_branch else f"{repo.name} [{branch}]"

    return ActivityRecord(
        id=f"github:commit:{commit.sha}",
        source="github",
        # repo name is the main thing distinguishing one commit from another
        # once several repos share the store, so it belongs in the embedded text
        title=f"{label}: {subject}",
        body=f"{repo.full_name} ({branch})\n\n{message}",
        timestamp=commit.commit.author.date.isoformat(),
        url=commit.html_url,
        raw=commit.raw_data,
    )


def _repo_commits(repo, username: str, since: datetime) -> list[ActivityRecord]:
    """Commits by this user anywhere in the repo, not just on the default branch.

    get_commits() without a sha only sees the default branch, which is exactly where
    work destined for a pull request is not: it sits on the topic branch until the
    PR merges, and in a fork the default branch tracks upstream instead.
    """
    records = []
    seen: set[str] = set()

    branches = [repo.default_branch]
    try:
        for branch in repo.get_branches()[:MAX_BRANCHES_PER_REPO]:
            if branch.name != repo.default_branch:
                branches.append(branch.name)
    except GithubException:
        # no branch listing (empty repo) -- the default branch attempt below still runs
        pass

    for branch in branches:
        try:
            for commit in repo.get_commits(sha=branch, since=since, author=username):
                if commit.sha in seen:
                    continue
                seen.add(commit.sha)
                records.append(_commit_record(repo, commit, branch))
        except GithubException as error:
            # empty repos 409, and a branch can vanish between listing and reading
            print(f"[github] skipped {repo.full_name}@{branch}: {error.data.get('message', error)}")
            continue

    return records


def fetch_recent_commits(since: datetime) -> list[ActivityRecord]:
    """Commits authored by the authenticated user across every repo they own."""
    gh = Github(github_token())
    username = gh.get_user().login

    records = []

    for repo in gh.get_user().get_repos(affiliation="owner"):
        if not _repo_has_activity_since(repo, since):
            continue
        records.extend(_repo_commits(repo, username, since))

    records.sort(key=lambda record: date_parser.parse(record.timestamp), reverse=True)
    return records
