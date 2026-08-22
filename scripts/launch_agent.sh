#!/usr/bin/env bash
# Run the menubar app without keeping the repo open in a terminal.
#
#   scripts/launch_agent.sh install          -- set up, you start it yourself
#   scripts/launch_agent.sh install --login  -- ...and start it at every login
#
#   scripts/launch_agent.sh start      -- start it now
#   scripts/launch_agent.sh stop       -- stop it now
#   scripts/launch_agent.sh status     -- installed? running? last exit code?
#   scripts/launch_agent.sh logs       -- tail what it printed
#   scripts/launch_agent.sh perms      -- what to grant for the hotkey to work
#   scripts/launch_agent.sh uninstall  -- remove it entirely

set -euo pipefail

LABEL="com.secondbrain.menubar"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

die() { echo "error: $*" >&2; exit 1; }

is_loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

write_plist() {
    local at_login="$1"

    mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"

    # In login mode it also restarts itself if it crashes. In manual mode it must
    # stay dead once stopped, so KeepAlive is left out entirely -- with it set,
    # stopping the app would just bring it straight back.
    local keepalive=""
    if [ "$at_login" = "true" ]; then
        keepalive="
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
"
    fi

    cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>-m</string>
        <string>src.capture.menubar_app</string>
    </array>

    <!-- credentials.json, token.json, second-brain.db and icons/ are all resolved
         relative to the working directory, so this has to be the repo root -->
    <key>WorkingDirectory</key>
    <string>$REPO</string>

    <key>RunAtLoad</key>
    <$at_login/>
$keepalive
    <key>ProcessType</key>
    <string>Interactive</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>

    <key>StandardOutPath</key>
    <string>$REPO/logs/menubar.out.log</string>
    <key>StandardErrorPath</key>
    <string>$REPO/logs/menubar.err.log</string>
</dict>
</plist>
PLIST_EOF
}

load_agent() {
    # bootout is asynchronous: bootstrapping while the old job is still going away
    # fails with "Input/output error", so wait for it to actually be gone
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    for _ in $(seq 20); do
        is_loaded || break
        sleep 0.25
    done
    launchctl bootstrap "$DOMAIN" "$PLIST"
}

install_agent() {
    [ -x "$PYTHON" ] || die "no interpreter at $PYTHON -- create the venv first"

    local at_login="false"
    [ "${1:-}" = "--login" ] && at_login="true"

    write_plist "$at_login"
    load_agent

    if [ "$at_login" = "true" ]; then
        echo "installed: starts now and at every login."
    else
        echo "installed: start it yourself with '$0 start'."
        echo "it will not start on its own, including at login."
    fi
    echo
    permissions_note
}

start_agent() {
    [ -f "$PLIST" ] || die "not installed -- run '$0 install' first"
    is_loaded || launchctl bootstrap "$DOMAIN" "$PLIST"
    launchctl kickstart "$DOMAIN/$LABEL"
    echo "started. the menubar icon should appear within a second or two."
}

stop_agent() {
    # bootout rather than a signal: it stops the process and unloads the job, so
    # nothing brings it back until you ask
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    echo "stopped."
}

uninstall_agent() {
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed. it will not start at login again."
}

status_agent() {
    if [ -f "$PLIST" ]; then
        if grep -A1 RunAtLoad "$PLIST" | grep -q "<true/>"; then
            echo "install: yes (starts at login)"
        else
            echo "install: yes (manual start)"
        fi
    else
        echo "install: no"
    fi

    if is_loaded; then
        launchctl print "$DOMAIN/$LABEL" | awk '/^\t(state|pid) = |last exit code/ {sub(/^\t/, "  "); print}'
    else
        echo "  not loaded"
    fi
    pgrep -f "src.capture.menubar_app" >/dev/null && echo "running: yes" || echo "running: no"
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
Until Accessibility is granted, use "Start Recording" from the menu.
EOF
}

case "${1:-}" in
    install)   install_agent "${2:-}" ;;
    start)     start_agent ;;
    stop)      stop_agent ;;
    uninstall) uninstall_agent ;;
    status)    status_agent ;;
    perms)     permissions_note ;;
    logs)      tail -n 40 "$REPO/logs/menubar.err.log" "$REPO/logs/menubar.out.log" 2>/dev/null || echo "no logs yet" ;;
    *)         die "usage: $0 {install [--login]|start|stop|status|logs|perms|uninstall}" ;;
esac
