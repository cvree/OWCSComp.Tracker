#!/usr/bin/env bash
# ====================================================================
#  OWCS Comp Tracker — double-click this file to start (macOS), or run
#  ./start.command from a terminal (macOS/Linux).
#
#  It checks your tools, says plainly what is missing and how to fix
#  it, then starts the control room and opens the portal in your
#  browser. Nothing here downloads or changes anything on its own.
#
#  Press Ctrl+C to stop.
# ====================================================================
set -u
cd "$(dirname "$0")"

echo
echo "  OWCS Comp Tracker"
echo "  -----------------"
echo

# Python 3 under either name, so this works on a stock macOS box and on
# a Linux one where `python` is python3.
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "  Python 3.10 or newer is not installed (or not on PATH)."
  echo
  echo "  macOS:  brew install python"
  echo "  Linux:  sudo apt install python3   (or your package manager)"
  echo
  echo "  Then run this file again."
  read -r -p "  Press Enter to close. " _ || true
  exit 1
fi

echo "  Checking your tools..."
echo
"$PY" pipeline/preflight.py
status=$?
echo

# preflight already printed a remedy per failing check — don't repeat
# them, just refuse to open a portal that cannot actually do anything.
if [ "$status" -ne 0 ]; then
  echo "  ---------------------------------------------------------------"
  echo "  Something above is missing. Fix the items marked FAIL, then run"
  echo "  this file again. The full walkthrough is on the site under"
  echo "  \"Start here\" (start.html)."
  echo "  ---------------------------------------------------------------"
  echo
  read -r -p "  Press Enter to close. " _ || true
  exit 1
fi

echo "  Starting the control room. Your browser will open in a moment."
echo "  Leave THIS window open while you work — it is the program."
echo
exec "$PY" pipeline/serve.py
