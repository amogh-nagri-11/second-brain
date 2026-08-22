from datetime import datetime

from dateutil import parser as date_parser
from github import Github, GithubException

from src.config.env import GITHUB_TOKEN
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


def fetch_recent_commits(since: datetime) -> list[ActivityRecord]:
    """Commits authored by the authenticated user across every repo they own."""
    gh = Github(GITHUB_TOKEN)
    username = gh.get_user().login

    records = []

    for repo in gh.get_user().get_repos(affiliation="owner"):
        if not _repo_has_activity_since(repo, since):
            continue

        try:
            commits = repo.get_commits(since=since, author=username)
            for commit in commits:
                message = commit.commit.message
                subject = message.split("\n")[0] if message else message

                records.append(ActivityRecord(
                    id=f"github:commit:{commit.sha}",
                    source="github",
                    # repo name is the main thing distinguishing one commit from another
                    # once several repos share the store, so it belongs in the embedded text
                    title=f"{repo.name}: {subject}",
                    body=f"{repo.full_name}\n\n{message}",
                    timestamp=commit.commit.author.date.isoformat(),
                    url=commit.html_url,
                    raw=commit.raw_data,
                ))
        except GithubException as error:
            # empty repos 409 and repos without commits by this author come back empty;
            # neither should abort the whole source
            print(f"[github] skipped {repo.full_name}: {error.data.get('message', error)}")
            continue

    records.sort(key=lambda record: date_parser.parse(record.timestamp), reverse=True)
    return records
