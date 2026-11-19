#!/bin/zsh
# Starts the feeder on a Mac, detached, for the long run (D-0024).
#
#   open -a Terminal scripts/start_feeder.command
#
# Start it this way, never from a Claude Code session's shell. macOS's Local Network privacy
# judges each connection to the home network by the program that launched the process; a
# feeder launched by a session loses the clock when that session closes (it keeps reaching
# Whisker, so it looks alive). Launched by Terminal, it does not depend on any session.
#
# Your clock's address: change this to yours. (This one is Kyle's clock, the one in
# docs/STATUS.md; change it here if the router moves it.)
CLOCK=http://192.168.86.41

cd "${0:A:h}/.." || exit 1
if pgrep -f "python -m arthur.feeder" >/dev/null; then
  echo "A feeder is already running; not starting a second one (pkill -f arthur.feeder stops it)."
  exit 1
fi

# A script opened by Terminal does not get the login shell's PATH, so look where uv installs.
UV=""
for candidate in "${commands[uv]}" /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv" \
    "$HOME/.cargo/bin/uv"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then UV=$candidate; break; fi
done
if [[ -z "$UV" ]]; then
  echo "uv was not found. Install it (https://docs.astral.sh/uv/) and try again."
  exit 1
fi

mkdir -p logs
nohup /usr/bin/caffeinate -i "$UV" run --env-file .env python -m arthur.feeder \
  --clock "$CLOCK" >> logs/feeder-console.log 2>&1 < /dev/null &
disown
echo "Arthur's feeder started (pid $!) against $CLOCK. Messages go to logs/feeder-console.log."
echo "You can close this window; the feeder keeps running."
