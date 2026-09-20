"""Where the app keeps its files.

Everything used to be resolved against the working directory, which only works
when that happens to be the repo root -- and an installed app can't write to its
own folder at all on Windows. Data lives in the per-user folder each OS expects:

    macOS    ~/Library/Application Support/SecondBrain
    Windows  %LOCALAPPDATA%\\SecondBrain
    Linux    ~/.local/share/SecondBrain

SECOND_BRAIN_HOME overrides it, which is what the tests use.
"""

import json
import os
import shutil
import sqlite3
import sys
import threading
from pathlib import Path

from platformdirs import user_data_dir, user_log_dir

APP_NAME = "SecondBrain"

# the repo root, for files that ship with the code rather than belong to the user
REPO_ROOT = Path(__file__).resolve().parents[2]
ICONS_DIR = REPO_ROOT / "icons"

# files that used to live in the repo root, under the names they had there
DB_NAME = "second-brain.db"
GOOGLE_TOKEN_NAME = "token.json"
GOOGLE_CLIENT_NAME = "credentials.json"
_LEGACY_FILES = (DB_NAME, GOOGLE_TOKEN_NAME, GOOGLE_CLIENT_NAME)

_ready_lock = threading.Lock()
_ready: set[Path] = set()


def data_dir() -> Path:
    override = os.environ.get("SECOND_BRAIN_HOME")
    path = Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False))

    with _ready_lock:
        if path not in _ready:
            path.mkdir(parents=True, exist_ok=True)
            if not override:
                _copy_legacy_files(REPO_ROOT, path)
            _ready.add(path)

    return path


def log_dir() -> Path:
    path = Path(user_log_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / DB_NAME


def google_token_path() -> Path:
    return data_dir() / GOOGLE_TOKEN_NAME


def google_client_path() -> Path:
    return data_dir() / GOOGLE_CLIENT_NAME


def _copy_legacy_files(source_dir: Path, target_dir: Path, log=print):
    """Copy, never move: the originals stay where they were until you delete them,
    so going back to an older checkout still finds its data. Anything already in
    the new folder wins -- it is newer than the copy in the repo."""
    for name in _LEGACY_FILES:
        source = source_dir / name
        target = target_dir / name
        if not source.is_file() or target.exists():
            continue

        if name.endswith(".db"):
            # sqlite's backup API rather than a file copy, so a copy taken while the
            # old app still has the database open is consistent
            with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
                src.backup(dst)
            src.close()
            dst.close()
        else:
            shutil.copy2(source, target)

        log(f"[paths] copied {source} -> {target}")


def _obsidian_config() -> Path:
    """Where Obsidian keeps its list of vaults, per OS."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/obsidian/obsidian.json"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", home)) / "obsidian/obsidian.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "obsidian/obsidian.json"


def obsidian_vault() -> Path | None:
    """The vault you opened most recently, if Obsidian is installed.

    A guess at a sensible default, nothing more -- the notes folder is a setting,
    and this is only what it falls back to.
    """
    config = _obsidian_config()
    try:
        vaults = json.loads(config.read_text(encoding="utf-8")).get("vaults", {})
    except (OSError, ValueError, AttributeError):
        return None

    if not isinstance(vaults, dict) or not vaults:
        return None

    newest = max(vaults.values(), key=lambda vault: vault.get("ts", 0) if isinstance(vault, dict) else 0)
    path = newest.get("path") if isinstance(newest, dict) else None
    return Path(path) if path else None
