#!/usr/bin/env bash
# Run the full pipeline immediately (real SerpApi + real Claude + real Slack).
# Useful for one-off testing outside the daily schedule.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/local_run.py" "$@"
