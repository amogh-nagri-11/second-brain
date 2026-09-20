"""Small, boring settings.

Credentials go in the keychain and paths to the app's own files are worked out,
but a few things are simply choices -- where your notes live, for one. They sit
in settings.json next to the database, readable and editable by hand.

    python -m src.config.settings                 what is set
    python -m src.config.settings notes_dir ~/x   set one
"""

import json
import sys
from pathlib import Path

from src.config.paths import data_dir

FILE_NAME = "settings.json"


def _path() -> Path:
    return data_dir() / FILE_NAME


def all_settings() -> dict:
    path = _path()
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # a hand-edited file with a stray comma shouldn't stop the app starting
        print(f"[settings] ignoring unreadable {path}")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def get(name: str, default=None):
    value = all_settings().get(name)
    return default if value is None else value


def set_value(name: str, value) -> Path:
    path = _path()
    settings = all_settings()
    if value is None:
        settings.pop(name, None)
    else:
        settings[name] = value
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    if not argv:
        settings = all_settings()
        print(f"{_path()}\n")
        for name, value in sorted(settings.items()):
            print(f"  {name} = {value}")
        if not settings:
            print("  (nothing set)")
        return 0

    name, *rest = argv
    if not rest:
        print(get(name, "(not set)"))
        return 0

    path = set_value(name, rest[0])
    print(f"{name} = {rest[0]}  ({path})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
