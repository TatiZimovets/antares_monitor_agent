#!/usr/bin/env bash
# Remove the Antares launchd jobs (monitor, stats, listener).

set -euo pipefail

for LABEL in io.antares.monitor io.antares.stats io.antares.listener; do
  TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
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
done
