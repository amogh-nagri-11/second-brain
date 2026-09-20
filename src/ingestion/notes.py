"""Your own notes, read from a folder of markdown files.

Every other source records what you did. This one records what you wrote down,
which is the half that says why -- the note arguing for a design and the commits
that carried it out finally sit in the same store.

Read-only, deliberately: files are opened and never written, so nothing here can
damage a vault you have kept for years. Writing notes ("remember that...") is a
separate thing, and it would write new files in one folder of its own rather than
touch what is already there.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as date_parser

from src.config.paths import obsidian_vault
from src.config.settings import get as setting
from src.ingestion.base import Source
from src.storage.types import ActivityRecord

SUFFIXES = {".md", ".markdown", ".txt"}
# folders that hold machinery rather than notes
SKIP_DIRS = {".obsidian", ".trash", ".git", ".stversions", "node_modules", "__pycache__"}
# a file bigger than this is a pasted dataset or an export, not a note; it would
# chunk into hundreds of pieces and drown everything else
MAX_BYTES = 200_000

FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\s*\n", re.S)
FRONT_MATTER_LINE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
# 2026-09-20, or 2026-09-20-standup: how daily notes are usually named
DATED_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


class NotesFolderUnreadable(RuntimeError):
    """Said out loud rather than swallowed: on macOS a folder under ~/Documents
    needs Full Disk Access before a background service can read it at all, and an
    empty sync would otherwise look like an empty folder."""


def notes_dir() -> Path | None:
    """Where your notes are: the setting, an override, or the Obsidian vault."""
    override = os.environ.get("SECOND_BRAIN_NOTES") or setting("notes_dir")
    if override:
        return Path(override).expanduser()
    return obsidian_vault()


def _front_matter(text: str) -> tuple[dict, str]:
    """The `---` block at the top, as far as it is worth reading.

    Parsed by hand rather than with a YAML library: what is wanted is a date and
    some tags, and a note written by a person is not a config file worth a
    dependency and a parse error.
    """
    match = FRONT_MATTER.match(text)
    if not match:
        return {}, text

    fields = {}
    for line in match.group(1).splitlines():
        found = FRONT_MATTER_LINE.match(line.strip())
        if not found:
            continue
        key, value = found.group(1).lower(), found.group(2).strip()
        fields[key] = value.strip("\"'")
    return fields, text[match.end():]


def _tags(front: dict) -> list[str]:
    raw = front.get("tags", "")
    return [tag.strip().lstrip("#") for tag in raw.strip("[]").split(",") if tag.strip()]


def _written_at(path: Path, front: dict, stat: os.stat_result) -> datetime:
    """When the note is about. Recency is a third of the ranking, so a daily note
    should sit on its own day rather than on the day you last touched it."""
    for candidate in (front.get("date"), front.get("created")):
        if candidate:
            try:
                moment = date_parser.parse(candidate)
                return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
            except (ValueError, OverflowError):
                pass

    named = DATED_NAME.search(path.stem)
    if named:
        return datetime.fromisoformat(named.group(1)).replace(tzinfo=timezone.utc)

    return datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)


def _title(path: Path, front: dict, body: str) -> str:
    if front.get("title"):
        return front["title"]
    heading = HEADING.search(body)
    if heading:
        return heading.group(1)
    return path.stem


def note_record(path: Path, root: Path) -> ActivityRecord | None:
    stat = path.stat()
    if stat.st_size > MAX_BYTES:
        return None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None

    front, body = _front_matter(text)
    relative = path.relative_to(root)
    folder = str(relative.parent) if str(relative.parent) != "." else ""

    return ActivityRecord(
        id=f"note:{relative.as_posix()}",
        source="notes",
        kind="note",
        timestamp=_written_at(path, front, stat).isoformat(),
        title=_title(path, front, body),
        # the folder is part of what a note means -- "Meetings/fleet sync" is not
        # the same note as "Ideas/fleet sync" -- so it is embedded with the text
        body=f"{relative.as_posix()}\n\n{body.strip()}",
        url=path.as_uri(),
        fields={
            "path": relative.as_posix(),
            "folder": folder,
            "tags": _tags(front),
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "words": len(body.split()),
        },
    )


def fetch_recent_notes(since: datetime) -> list[ActivityRecord]:
    """Every note in the folder, whatever `since` says.

    A folder scan is cheap and unchanged files are skipped before they are
    embedded, so there is nothing to gain by filtering on modified time -- and
    plenty to lose: deletions are worked out from what a fetch returns, so a fetch
    that returned only recent notes would have the rest treated as deleted.
    """
    root = notes_dir()
    if root is None:
        return []

    root = root.expanduser()
    if not root.is_dir():
        raise NotesFolderUnreadable(f"{root} is not a folder -- set one with: python -m src notes <path>")

    def blew_up(error: OSError):
        # os.walk swallows errors by default, so a folder macOS won't let us read
        # comes back as an empty one -- which looks exactly like having no notes
        raise error

    records = []
    try:
        for dirpath, dirnames, filenames in os.walk(root, onerror=blew_up):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not name.startswith(".")]
            for filename in filenames:
                path = Path(dirpath) / filename
                if path.suffix.lower() not in SUFFIXES or filename.startswith("."):
                    continue
                record = note_record(path, root)
                if record is not None:
                    records.append(record)
    except PermissionError as error:
        raise NotesFolderUnreadable(
            f"can't read {root}: {error}. On macOS a folder under ~/Documents, ~/Desktop or"
            " ~/Downloads needs Full Disk Access -- see: python -m src notes"
        ) from error

    return records


SOURCE = Source(
    name="notes",
    description="markdown notes from a folder, read and never written",
    configured=lambda: notes_dir() is not None and notes_dir().expanduser().is_dir(),
    fetch=fetch_recent_notes,
    kinds=("note",),
    # a scan sees the whole folder however old the notes are, so one that stops
    # coming back has been deleted -- even if it was written years ago
    complete_history=True,
)
