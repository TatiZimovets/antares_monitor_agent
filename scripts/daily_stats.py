"""Post the evening Antares summary to Slack: today's funnel + last 7 days.

Runs daily at 19:00 under launchd (io.antares.stats).

    python scripts/daily_stats.py            # post to Slack
    python scripts/daily_stats.py --print    # print the numbers only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.leads import LeadsDB  # noqa: E402
from src.slack import send_daily_stats  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", help="print the numbers, don't post")
    args = parser.parse_args()

    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    leads = LeadsDB()
    today = leads.stats(int(midnight.timestamp()), int(now.timestamp()) + 1)
    week = leads.stats(int((midnight - timedelta(days=6)).timestamp()), int(now.timestamp()) + 1)
    day = now.strftime("%b %d")

    if args.print:
        print(json.dumps({"day": day, "today": today, "last_7_days": week}, indent=2))
        return 0

    cfg = load_config()
    if not cfg.slack_bot_token or not cfg.slack_channel_id:
        logging.error("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID are required")
        return 1
    result = send_daily_stats(cfg.slack_bot_token, cfg.slack_channel_id, day, today, week)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
