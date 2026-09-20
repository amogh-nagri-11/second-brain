"""Credentials, looked up only when the thing that needs them runs.

Nothing is required at import: someone who only uses the calendar shouldn't need
a GitHub token to ask a question, and a missing API key should say so rather than
stop the app starting.

Each is looked for in order:
  1. the environment, so a one-off override needs no setup
  2. the OS keychain (Keychain, Windows Credential Manager, Secret Service)
  3. a .env file in the app folder, then in the repo root

    python -m src.config.env status            which are set, and where from
    python -m src.config.env set OPENROUTER_API_KEY   store one in the keychain
    python -m src.config.env import-env        copy what .env has into the keychain
"""

import getpass
import os
import sys
from pathlib import Path

import keyring
from dotenv import dotenv_values
from keyring.errors import KeyringError

from src.config.paths import REPO_ROOT, data_dir

KEYRING_SERVICE = "SecondBrain"

KNOWN = {
    "GITHUB_TOKEN": "GitHub personal access token, repo scope",
    "OPENROUTER_API_KEY": "OpenRouter API key, from openrouter.ai/keys",
}


class MissingCredential(RuntimeError):
    pass


def _env_files() -> list[Path]:
    return [data_dir() / ".env", REPO_ROOT / ".env"]


def _from_keyring(name: str) -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except KeyringError:
        # no usable backend, e.g. a headless Linux box without Secret Service
        return None


def _from_env_files(name: str) -> tuple[str, Path] | None:
    for path in _env_files():
        if path.is_file():
            value = dotenv_values(path).get(name)
            if value:
                return value, path
    return None


def lookup(name: str) -> tuple[str, str] | None:
    """(value, where it came from), or None."""
    if os.environ.get(name):
        return os.environ[name], "environment"
    if value := _from_keyring(name):
        return value, "keychain"
    if found := _from_env_files(name):
        return found[0], str(found[1])
    return None


def get_secret(name: str) -> str | None:
    found = lookup(name)
    return found[0] if found else None


def require(name: str) -> str:
    value = get_secret(name)
    if not value:
        raise MissingCredential(
            f"{name} isn't set. Store it with: python -m src.config.env set {name}"
        )
    return value


def github_token() -> str:
    return require("GITHUB_TOKEN")


def llm_api_key() -> str:
    """Whoever answers questions. OpenRouter by default -- see src.synthesis.llm."""
    return require("OPENROUTER_API_KEY")


def _main(argv: list[str]) -> int:
    command = argv[0] if argv else "status"

    if command == "status":
        for name, description in KNOWN.items():
            found = lookup(name)
            print(f"{name:14} {'set in ' + found[1] if found else 'not set':40} {description}")
        return 0

    if command == "set" and len(argv) == 2:
        value = getpass.getpass(f"{argv[1]}: ").strip()
        if not value:
            print("nothing entered, left unchanged")
            return 1
        keyring.set_password(KEYRING_SERVICE, argv[1], value)
        print(f"stored {argv[1]} in the keychain")
        return 0

    if command == "import-env":
        for name in KNOWN:
            found = _from_env_files(name)
            if found:
                keyring.set_password(KEYRING_SERVICE, name, found[0])
                print(f"stored {name} in the keychain (from {found[1]})")
        print("the .env file is left as it was; delete it once `status` shows the keychain")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
