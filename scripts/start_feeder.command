#!/bin/zsh
# Starts the feeder on the Mac, detached, for the long run (D-0024).
#
#   open -a Terminal scripts/start_feeder.command
#
# Start it this way, never from a Claude Code session's shell. macOS's Local Network privacy
# judges each connection to the home network by the program that launched the process; a
# feeder launched by a session loses the clock when that session closes (it keeps reaching
# Whisker, so it looks alive). Launched by Terminal, it does not depend on any session.
#
# The clock's address is the one in docs/STATUS.md; change it here if the router moves it.
CLOCK=http://192.168.86.41

cd "${0:A:h}/.." || exit 1
if pgrep -f "python -m arthur.feeder" >/dev/null; then
  echo "A feeder is already running; not starting a second one (pkill -f arthur.feeder stops it)."
  exit 1
fi
nohup /usr/bin/caffeinate -i /opt/homebrew/bin/uv run --env-file .env python -m arthur.feeder \
  --clock "$CLOCK" >> logs/feeder-console.log 2>&1 < /dev/null &
disown
echo "Arthur's feeder started (pid $!) against $CLOCK. Messages go to logs/feeder-console.log."
echo "You can close this window; the feeder keeps running."
