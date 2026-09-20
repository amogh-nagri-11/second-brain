from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser
from github import Github, GithubException

from src.config.env import get_secret, github_token
from src.ingestion.base import Source
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
        kind="commit",
        # repo name is the main thing distinguishing one commit from another
        # once several repos share the store, so it belongs in the embedded text
        title=f"{label}: {subject}",
        body=f"{repo.full_name} ({branch})\n\n{message}",
        timestamp=commit.commit.author.date.isoformat(),
        url=commit.html_url,
        fields={
            "repo": repo.name,
            "full_name": repo.full_name,
            "branch": branch,
            "merged": branch == repo.default_branch,
            "sha": commit.sha,
        },
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


def merged_commits(candidates: list[tuple[str, str, str]]) -> set[str]:
    """Which of these (id, owner/repo, sha) commits have reached their repo's
    default branch since they were seen on a topic branch.

    Asks GitHub to compare the default branch with the commit: "behind" or
    "identical" means the default branch already contains it. A branch merged by
    squash or rebase gets new commits, so its originals never show up this way;
    they keep their branch label.
    """
    gh = Github(github_token())
    by_repo: dict[str, list[tuple[str, str]]] = {}
    for item_id, full_name, sha in candidates:
        by_repo.setdefault(full_name, []).append((item_id, sha))

    merged: set[str] = set()
    for full_name, commits in by_repo.items():
        try:
            repo = gh.get_repo(full_name)
            default = repo.default_branch
        except GithubException as error:
            print(f"[github] can't check {full_name}: {error.data.get('message', error)}")
            continue
        for item_id, sha in commits:
            try:
                comparison = repo.compare(default, sha)
            except GithubException:
                # the commit is gone -- a force-push or a deleted fork
                continue
            if comparison.status in ("behind", "identical"):
                merged.add(item_id)
    return merged


def as_merged(record: ActivityRecord) -> ActivityRecord:
    """The same commit, relabelled now that it's on the default branch."""
    label, _, subject = record.title.partition(": ")
    repo_name = label.split(" [", 1)[0]
    return record.model_copy(update={
        "title": f"{repo_name}: {subject}",
        "fields": {**record.fields, "merged": True},
    })


# how far back, and how many, unmerged commits are re-checked each sync
MERGE_CHECK_DAYS = 180
MERGE_CHECK_LIMIT = 50


def update_merged(conn, log) -> int:
    """Relabel commits that have reached their default branch since we stored them.

    A commit's title carries its branch, which is how "what hasn't landed" is
    answered, so a stored one goes stale the moment its branch merges. Kept here
    rather than in sync because it is a fact about GitHub, not about syncing.
    """
    from src.storage.db import load_item, save_item, unmerged_commits
    from src.sync import embed_chunks

    since = (datetime.now(timezone.utc) - timedelta(days=MERGE_CHECK_DAYS)).isoformat()
    candidates = unmerged_commits(conn, since, MERGE_CHECK_LIMIT)
    if not candidates:
        return 0

    merged = merged_commits(candidates)
    for item_id in merged:
        record = as_merged(load_item(conn, item_id))
        save_item(conn, record, embed_chunks(record.title, record.body))
    if merged:
        log(f"[sync] github: {len(merged)} branch commits have since merged")
    return len(merged)


SOURCE = Source(
    name="github",
    description="commits you authored, across your repos and their branches",
    configured=lambda: bool(get_secret("GITHUB_TOKEN")),
    fetch=fetch_recent_commits,
    kinds=("commit",),
    after=update_merged,
)
