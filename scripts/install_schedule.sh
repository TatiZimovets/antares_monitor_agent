#!/usr/bin/env bash
# Install the launchd daily schedule for the Antares Monitor pipeline.
#
# Usage:
#   scripts/install_schedule.sh
#
# Reversible via scripts/uninstall_schedule.sh. No root / sudo needed —
# LaunchAgents run as the current user.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/infra/launchd/io.antares.monitor.plist.template"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/io.antares.monitor.plist"
LABEL="io.antares.monitor"

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "error: $PROJECT_DIR/.venv/bin/python not found"
  echo "create the venv first:  python3 -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

mkdir -p "$TARGET_DIR" "$PROJECT_DIR/logs"

# Substitute the project path into the template.
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$TEMPLATE" > "$TARGET_PLIST"

# Unload if already present (idempotent), then load.
if launchctl list | grep -q "$LABEL"; then
  launchctl unload "$TARGET_PLIST" 2>/dev/null || true
fi
launchctl load "$TARGET_PLIST"

echo "installed: $TARGET_PLIST"
echo "status:    $(launchctl list | grep "$LABEL" || echo 'not listed (check logs)')"
echo
echo "next run:  daily at 09:00 local time (next wake if asleep)"
echo "logs:      $PROJECT_DIR/logs/monitor.out.log"
echo "           $PROJECT_DIR/logs/monitor.err.log"
echo
echo "run immediately:  launchctl start $LABEL"
echo "                  # or: scripts/run_now.sh"
echo "stop schedule:    scripts/uninstall_schedule.sh"
