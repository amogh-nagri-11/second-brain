"""python -m src <command>

    serve               run the service in the foreground
    ask "question"      ask by text; prints the written answer. Follows on from
                        your recent questions -- pass --new to start a topic
    new                 forget the conversation so far
    notes [path]        where notes are read from; pass a path to change it
    record [on|off]     start or stop listening; toggles with no argument --
                        this is the command to bind to a keyboard shortcut
    stop                stop the running service
    hush                stop speaking, mid-answer
    sync                sync now
    status              is it running, and what is it doing
    open                open the window in your browser
    service install     start the service at login (--menubar on macOS adds the menu)
    service uninstall | start | stop | status | logs
    service hotkey      how to bind a key to `record` on this OS

Everything but `serve` talks to the running service, so it starts instantly and
works from any shell or shortcut.
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    # run as a file -- how a keyboard shortcut calls it, from no particular
    # directory -- so put the repo root where `import src` can find it
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ago(moment: float | None) -> str:
    if moment is None:
        return "never"
    minutes = int(time.time() - moment) // 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    return f"{minutes // 60}h ago"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m src", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    serve = commands.add_parser("serve", help="run the service in the foreground")
    serve.add_argument("--log-file", action="store_true", help="write output to the service log")

    ask = commands.add_parser("ask", help="ask by text")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--new", action="store_true", help="start a new topic, ignoring recent questions")

    record = commands.add_parser("record", help="start, stop or toggle listening")
    record.add_argument("state", nargs="?", choices=["on", "off"])

    commands.add_parser("stop", help="stop the running service")
    commands.add_parser("hush", help="stop speaking, mid-answer")
    commands.add_parser("new", help="start a new topic; the next question stands alone")

    notes = commands.add_parser("notes", help="where your notes are read from")
    notes.add_argument("path", nargs="?", help="set the folder")
    commands.add_parser("sync", help="sync now")
    commands.add_parser("status", help="what the service is doing")
    commands.add_parser("open", help="open the window")

    service = commands.add_parser("service", help="run at login, and manage it")
    service.add_argument("action", choices=["install", "uninstall", "start", "stop", "status", "logs", "hotkey"])
    service.add_argument("--menubar", action="store_true", help="macOS: also start the menubar app")

    args = parser.parse_args(argv)

    if args.command == "service":
        return _service(args)

    if args.command == "serve":
        from src.core.serve import serve
        return serve(log_file=args.log_file)

    from src.core import client

    try:
        if args.command == "ask":
            entry = client.call(
                "POST", "/api/ask", {"question": " ".join(args.question), "new_topic": args.new}
            )
            print(entry["written"] or entry["spoken"])

        elif args.command == "record":
            wanted = {"on": True, "off": False}.get(args.state)
            result = client.call("POST", "/api/record", {"recording": wanted})
            print("listening" if result["recording"] else "stopped")

        elif args.command == "hush":
            client.call("POST", "/api/stop")

        elif args.command == "stop":
            return _stop_service()

        elif args.command == "notes":
            return _notes(args)

        elif args.command == "new":
            client.call("POST", "/api/new-topic")
            print("new topic")

        elif args.command == "sync":
            client.call("POST", "/api/sync")
            print("syncing")

        elif args.command == "status":
            state = client.call("GET", "/api/state")
            doing = [name for name in ("recording", "thinking", "speaking", "syncing") if state[name]]
            if state.get("in_conversation"):
                doing.append("in a conversation")
            print(f"running at {client.base_url()}/")
            print(f"  {', '.join(doing) if doing else 'idle'}; last sync {_ago(state['last_sync_at'])}")
            if state["history"]:
                print(f"  last question: {state['history'][0]['question']}")

        elif args.command == "open":
            import webbrowser
            webbrowser.open(client.base_url() + "/")

    except client.ServiceNotRunning as error:
        print(error, file=sys.stderr)
        return 2
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


def _stop_service() -> int:
    """Stop a service started by hand.

    `service stop` is for the one launchd/systemd runs; this is for a `serve` in a
    terminal, which otherwise needed the process hunted down by hand. The pid is
    in the service file, so there is nothing to hunt.
    """
    import os
    import signal

    from src.core import client

    info = client.read_service_file()
    if info is None:
        print("not running")
        return 0

    pid = info.get("pid")
    if not pid:
        print("running, but the service file has no pid -- kill it with: pkill -f 'src serve'")
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # died without cleaning up after itself
        client.service_file().unlink(missing_ok=True)
        print("not running (stale service file removed)")
        return 0
    except PermissionError:
        print(f"not allowed to stop pid {pid} -- it belongs to another user")
        return 1

    # SIGTERM lets it shut down cleanly and remove its own service file; waiting
    # for that is how we know it is really gone rather than just asked to go
    for _ in range(50):
        if not client.is_running():
            print(f"stopped (pid {pid})")
            return 0
        time.sleep(0.1)

    os.kill(pid, signal.SIGKILL)
    client.service_file().unlink(missing_ok=True)
    print(f"killed (pid {pid}) -- it didn't stop when asked")
    return 0


def _notes(args) -> int:
    """Where notes are read from, and -- on macOS especially -- whether they can be."""
    from src.config.settings import set_value
    from src.ingestion.notes import NotesFolderUnreadable, fetch_recent_notes, notes_dir

    if args.path:
        folder = Path(args.path).expanduser()
        set_value("notes_dir", str(folder))
        print(f"notes folder: {folder}")
        if not folder.is_dir():
            print("  it isn't a folder yet -- nothing will be read until it is")
            return 0

    folder = notes_dir()
    if folder is None:
        print("no notes folder set, and no Obsidian vault found")
        print("  set one with: python -m src notes <path>")
        return 0

    print(f"notes folder: {folder}")
    try:
        records = fetch_recent_notes(datetime.now(timezone.utc))
    except NotesFolderUnreadable as error:
        print(f"  unreadable: {error}")
        # the fix is a permission the user grants, not something the app can do
        print(f"\n  System Settings > Privacy & Security > Full Disk Access, and add:\n    {sys.executable}")
        return 1

    print(f"  {len(records)} notes readable")
    for record in records[:3]:
        print(f"    - {record.fields['path']}")
    return 0


def _service(args) -> int:
    from src.config.paths import log_dir
    from src.core import autostart, client

    manager = autostart.backend()

    if args.action == "hotkey":
        print(autostart.hotkey_help())
    elif args.action == "install":
        manager.install(menubar=args.menubar)
        print("installed: starts at login, restarts if it crashes")
        print("bind a key next -- see: python -m src service hotkey")
    elif args.action == "uninstall":
        manager.uninstall()
        print("uninstalled")
    elif args.action == "start":
        manager.start()
    elif args.action == "stop":
        manager.stop()
    elif args.action == "status":
        print(f"installed: {'yes' if manager.installed() else 'no'}")
        print(f"running:   {client.base_url() + '/' if client.is_running() else 'no'}")
        print(f"log:       {log_dir() / 'service.log'}")
    elif args.action == "logs":
        path = log_dir() / "service.log"
        if not path.exists():
            print(f"no log yet at {path}")
            return 1
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(lines[-60:]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
