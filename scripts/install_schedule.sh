#!/usr/bin/env bash
# Install the launchd jobs for the Antares Monitor:
#   io.antares.monitor   daily 09:00  scan Reddit, post leads with buttons
#   io.antares.stats     daily 19:00  post the day summary
#   io.antares.listener  always on    handle Replied / Skip button clicks
#
# Usage:
#   scripts/install_schedule.sh
#
# Reversible via scripts/uninstall_schedule.sh. No root / sudo needed —
# LaunchAgents run as the current user.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$HOME/Library/LaunchAgents"
LABELS=(io.antares.monitor io.antares.stats io.antares.listener)

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "error: $PROJECT_DIR/.venv/bin/python not found"
  echo "create the venv first:  python3 -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

mkdir -p "$TARGET_DIR" "$PROJECT_DIR/logs"

for LABEL in "${LABELS[@]}"; do
  TEMPLATE="$PROJECT_DIR/infra/launchd/$LABEL.plist.template"
  TARGET_PLIST="$TARGET_DIR/$LABEL.plist"

  # Substitute the project path into the template.
  sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$TEMPLATE" > "$TARGET_PLIST"

  # Unload if already present (idempotent), then load.
  if launchctl list | grep -q "$LABEL"; then
    launchctl unload "$TARGET_PLIST" 2>/dev/null || true
  fi
  launchctl load "$TARGET_PLIST"
  echo "installed: $TARGET_PLIST"
done

echo
echo "status:"
launchctl list | grep io.antares || echo "  not listed (check logs)"
echo
echo "monitor:   daily at 09:00 local time (next wake if asleep)"
echo "summary:   daily at 19:00 local time"
echo "listener:  running now; needs SLACK_APP_TOKEN in .env"
echo "logs:      $PROJECT_DIR/logs/{monitor,stats,listener}.{out,err}.log"
echo
echo "run immediately:  launchctl start io.antares.monitor"
echo "                  # or: scripts/run_now.sh"
echo "stop schedule:    scripts/uninstall_schedule.sh"
