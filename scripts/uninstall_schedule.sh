#!/usr/bin/env bash
# Remove the launchd daily schedule.

set -euo pipefail

TARGET_PLIST="$HOME/Library/LaunchAgents/io.antares.monitor.plist"
LABEL="io.antares.monitor"

if [[ -f "$TARGET_PLIST" ]]; then
  launchctl unload "$TARGET_PLIST" 2>/dev/null || true
  rm -f "$TARGET_PLIST"
  echo "removed: $TARGET_PLIST"
else
  echo "nothing to remove: $TARGET_PLIST not found"
fi

if launchctl list | grep -q "$LABEL"; then
  echo "warning: $LABEL still listed in launchctl (may clear on next logout)"
fi
