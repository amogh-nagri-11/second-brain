second brain + voice bot in python

## Running it without a terminal

The menubar app can be run by a launch agent, so the repo never has to be open in
a terminal. By default you start it yourself:

    scripts/launch_agent.sh install     # set it up, don't start anything yet
    scripts/launch_agent.sh start       # start it now
    scripts/launch_agent.sh stop        # stop it, and it stays stopped

Add `--login` to the install if you'd rather have it always there:

    scripts/launch_agent.sh install --login

That starts it at every login and restarts it if it crashes, while still
respecting Quit from the menu -- a clean exit stays exited until the next login.

    scripts/launch_agent.sh status      # installed? running? last exit code?
    scripts/launch_agent.sh logs        # stdout/stderr from the agent
    scripts/launch_agent.sh perms       # what to grant for the F9 hotkey
    scripts/launch_agent.sh uninstall   # remove it entirely

One caveat worth knowing: run from a terminal, the F9 hotkey rode on the
terminal's Accessibility permission. Run by launchd, the Python interpreter needs
its own grant -- `perms` prints the exact path to add. Until then, recording still
works from the menu item.

### What it costs to leave running

Measured while idle: about 0.07% CPU and no idle wake-ups, so the per-second menu
refresh is not what to worry about. Memory is the real cost -- the speech-to-text
model alone is ~540MB resident, which is why it is now loaded on the first
question rather than at startup, and why syncing is hourly rather than
half-hourly.
