second brain + voice bot in python

## Running it without a terminal

The menubar app is normally started by a login agent, so the repo never has to be
open in a terminal:

    scripts/launch_agent.sh install

It starts immediately and at every login after that, restarting itself if it
crashes. Quitting from the menu keeps it quit until the next login.

    scripts/launch_agent.sh status      # loaded? running? last exit code?
    scripts/launch_agent.sh logs        # stdout/stderr from the agent
    scripts/launch_agent.sh perms       # what to grant for the F9 hotkey
    scripts/launch_agent.sh uninstall   # stop, and don't start at login again

One caveat worth knowing: run from a terminal, the F9 hotkey rode on the
terminal's Accessibility permission. Run by launchd, the Python interpreter needs
its own grant -- `perms` prints the exact path to add. Until then, recording still
works from the menu item.
