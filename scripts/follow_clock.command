#!/bin/zsh
# Keeps the clock's log on this Mac while the clock runs itself (D-0029).
#
#   open -a Terminal scripts/follow_clock.command
#
# The program on the clock keeps its log in memory (about 128 KB) and, since D-0035, on its
# flash too (about four days), so lines missed while this was away are fetched when it comes
# back, across restarts of the clock as well. This asks the clock for new lines every 2 s and appends them under logs/, where
# the feeder's logs are: the studio's Robot log source reads them the same way. It keeps
# trying while the clock or the Mac is away, and picks up where it left off.
#
# Start it this way, never from a Claude Code session's shell, for the same reason as
# scripts/start_feeder.command: macOS judges each connection to the home network by the
# program that launched the process.
#
# Your clock's address: change this to yours, or set CLOCK in scripts/start_feeder.local
# (the same file the feeder's start script reads).
CLOCK=http://192.168.86.41

cd "${0:A:h}/.." || exit 1
if [[ -f scripts/start_feeder.local ]]; then source scripts/start_feeder.local; fi
HOST=${${CLOCK#http://}%/}
if pgrep -f "scripts/clock.py --clock .* logs follow" >/dev/null; then
  echo "A log follower is already running; not starting a second one."
  echo "(pkill -f 'clock.py --clock .* logs follow' stops it.)"
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
nohup "$UV" run python scripts/clock.py --clock "$HOST" logs follow \
  >> logs/clock-follow.log 2>&1 < /dev/null &
disown
echo "Following the clock's log at $HOST (pid $!). New lines go under logs/;"
echo "the follower's own messages go to logs/clock-follow.log."
echo "You can close this window; it keeps running until the Mac restarts."
