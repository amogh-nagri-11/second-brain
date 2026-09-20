"""Pull requests you opened, anywhere on GitHub.

Kept apart from commit ingestion because the two cost completely different
things: commits are walked repo by repo and branch by branch, while one search
query returns every pull request you have ever opened, in any repo, including
ones you don't own. The search result already carries state, merge time, labels
and the repository, so a pull request costs no follow-up call.
"""

from datetime import datetime

from github import Auth, Github

from src.config.env import github_token
from src.storage.types import ActivityRecord

# search tops out at 1000 results anyway, and a personal history reaches nowhere
# near that; the cap is here so a surprise can't turn into an endless paging loop
MAX_RESULTS = 300


def _full_name(raw: dict) -> str:
    # "https://api.github.com/repos/huggingface/peft" -> "huggingface/peft". Taken
    # from the search result rather than issue.repository, which would cost a call
    # per pull request
    return raw.get("repository_url", "").split("/repos/", 1)[-1]


def _state(raw: dict) -> str:
    """merged / open / closed.

    GitHub's own `state` only knows open and closed, so a merged pull request and
    an abandoned one look identical there. The merge time is what separates them,
    and it is the distinction every question cares about.
    """
    if (raw.get("pull_request") or {}).get("merged_at"):
        return "merged"
    return "open" if raw.get("state") == "open" else "closed"


def pr_record(raw: dict, username: str) -> ActivityRecord:
    full_name = _full_name(raw)
    owner, _, repo = full_name.partition("/")
    # your own repos and other people's are both your work, but they answer
    # different questions -- "what have I contributed to" versus "what did I do on
    # my own projects" -- so which it is gets stored rather than inferred from the
    # name at question time
    ownership = "own" if owner.lower() == username.lower() else "external"

    state = _state(raw)
    merged_at = (raw.get("pull_request") or {}).get("merged_at")
    number = raw.get("number")
    draft = bool(raw.get("draft"))
    labels = [label["name"] for label in raw.get("labels") or []]

    # a pull request on someone else's repo is named by its full path, so "peft"
    # and a fork of it don't read the same; the state rides along in the title
    # because that is the part a question like "what's still open" matches on
    where = repo if ownership == "own" else full_name
    marks = [state] + (["draft"] if draft else []) + ([] if ownership == "own" else ["external"])
    title = f"{where} #{number} ({', '.join(marks)}): {raw.get('title') or ''}"

    header = f"{full_name} · pull request #{number} · {state}"
    if labels:
        header += f" · {', '.join(labels)}"

    return ActivityRecord(
        id=f"github:pr:{full_name}#{number}",
        source="github",
        kind="pr",
        # merged work belongs at the moment it landed; anything else at the moment
        # you opened it, so "latest" and "this week" line up with what you did
        timestamp=merged_at or raw.get("created_at"),
        title=title,
        body=f"{header}\n\n{(raw.get('body') or '').strip()}",
        url=raw.get("html_url"),
        fields={
            "repo": repo,
            "full_name": full_name,
            "number": number,
            "state": state,
            "merged": state == "merged",
            "ownership": ownership,
            "draft": draft,
            "labels": labels,
            "opened_at": raw.get("created_at"),
            "merged_at": merged_at,
            "closed_at": raw.get("closed_at"),
        },
        raw=raw,
    )


def fetch_recent_prs(since: datetime) -> list[ActivityRecord]:
    """Every pull request of yours touched since `since`.

    Filtered on when the pull request was last updated, not when it was opened:
    one you opened months ago and merged yesterday has to come back, or it would
    stay stored as open forever.
    """
    gh = Github(auth=Auth.Token(github_token()))
    username = gh.get_user().login

    # search only understands whole days, so the window is rounded outwards
    query = f"is:pr author:{username} updated:>={since:%Y-%m-%d}"
    results = gh.search_issues(query, sort="updated", order="desc")

    records = []
    for issue in results[:MAX_RESULTS]:
        records.append(pr_record(issue.raw_data, username))
    return records
