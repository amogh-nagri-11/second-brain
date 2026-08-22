#!/usr/bin/env bash
# Install / remove the login agent that keeps the menubar app running.
#
#   scripts/launch_agent.sh install    -- start now and at every login
#   scripts/launch_agent.sh uninstall  -- stop and never start again
#   scripts/launch_agent.sh status     -- is it loaded, is it running
#   scripts/launch_agent.sh logs       -- tail what it printed
#   scripts/launch_agent.sh perms      -- what to grant for the hotkey to work
#
# Nothing here needs the repo to be open in a terminal; that is the point.

set -euo pipefail

LABEL="com.secondbrain.menubar"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

die() { echo "error: $*" >&2; exit 1; }

install_agent() {
    [ -x "$PYTHON" ] || die "no interpreter at $PYTHON -- create the venv first"

    mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"

    sed -e "s|__LABEL__|$LABEL|g" \
        -e "s|__PYTHON__|$PYTHON|g" \
        -e "s|__WORKDIR__|$REPO|g" \
        "$REPO/scripts/$LABEL.plist.template" > "$PLIST"

    # bootout first so re-running install picks up an edited plist rather than
    # silently keeping the old one loaded
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true

    # bootout is asynchronous: bootstrapping while the old job is still going away
    # fails with "Input/output error", so wait for it to actually be gone
    for _ in $(seq 20); do
        launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
        sleep 0.25
    done

    launchctl bootstrap "$DOMAIN" "$PLIST"

    echo "installed: $PLIST"
    echo "the menubar icon should appear within a second or two."
    echo
    permissions_note
}

permissions_note() {
    # Launched from Terminal, the hotkey rode on Terminal.app's Accessibility grant.
    # Run directly by launchd, the interpreter needs its own -- and TCC identifies it
    # by the real binary, not the venv symlink.
    local real
    real="$(python3 -c "import os,sys;print(os.path.realpath('$PYTHON'))" 2>/dev/null || echo "$PYTHON")"
    cat <<EOF
The F9 hotkey needs Accessibility permission for the interpreter itself:

  System Settings > Privacy & Security > Accessibility > "+"
  then Cmd+Shift+G and paste:

    $real

Microphone access is prompted for separately the first time you record.
Until Accessibility is granted, use "Start/Stop Recording" from the menu.
EOF
}

uninstall_agent() {
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed. it will not start at login again."
}

status_agent() {
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
        echo "loaded:  yes"
        launchctl print "$DOMAIN/$LABEL" | awk '/^\t(state|pid) = |last exit code/ {sub(/^\t/, "  "); print}'
    else
        echo "loaded:  no"
    fi
    pgrep -f "src.capture.menubar_app" >/dev/null && echo "running: yes" || echo "running: no"
}

case "${1:-}" in
    install)   install_agent ;;
    uninstall) uninstall_agent ;;
    status)    status_agent ;;
    perms)     permissions_note ;;
    logs)      tail -n 40 "$REPO/logs/menubar.err.log" "$REPO/logs/menubar.out.log" 2>/dev/null || echo "no logs yet" ;;
    *)         die "usage: $0 {install|uninstall|status|logs|perms}" ;;
esac
