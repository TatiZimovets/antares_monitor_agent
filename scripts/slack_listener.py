"""Socket Mode listener for the Replied / Skip / Undo buttons on lead cards.

Runs on this Mac under launchd (io.antares.listener, kept alive). Socket Mode
needs no public URL: Slack pushes clicks over a websocket opened with the
app-level token (SLACK_APP_TOKEN). Clicks made while the Mac is asleep fail
in Slack and can simply be clicked again.

    python scripts/slack_listener.py
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

import certifi

# python.org builds of Python don't ship a trust store; the builtin Socket
# Mode client opens its own TLS socket, so point OpenSSL at certifi's bundle.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slack_sdk import WebClient  # noqa: E402
from slack_sdk.socket_mode.builtin import SocketModeClient  # noqa: E402
from slack_sdk.socket_mode.request import SocketModeRequest  # noqa: E402
from slack_sdk.socket_mode.response import SocketModeResponse  # noqa: E402

from src.config import load_config  # noqa: E402
from src.leads import LeadsDB  # noqa: E402
from src.slack import (  # noqa: E402
    _SSL_CONTEXT,
    LEAD_ACTIONS_BLOCK_ID,
    LEAD_STATUS_BLOCK_ID,
    lead_actions_block,
    lead_status_block,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("listener")

ACTION_STATUS = {"lead_replied": "replied", "lead_skipped": "skipped", "lead_undo": "new"}


def _swap_status_block(blocks: list[dict], new_block: dict) -> list[dict]:
    """Replace the card's buttons / status line with `new_block`."""
    kept = [b for b in blocks if b.get("block_id") not in (LEAD_ACTIONS_BLOCK_ID, LEAD_STATUS_BLOCK_ID)]
    return kept + [new_block]


def handle_click(web: WebClient, leads: LeadsDB, payload: dict) -> None:
    action = payload["actions"][0]
    status = ACTION_STATUS.get(action.get("action_id"))
    if status is None:
        return
    key = action["value"]
    user_id = payload["user"]["id"]

    lead = leads.set_status(key, status, by=payload["user"].get("username", user_id))
    if lead is None:
        log.warning("click for unknown lead %s (posted before tracking started?)", key)

    if status == "new":
        new_block = lead_actions_block(key)
    else:
        new_block = lead_status_block(key, status, user_id, datetime.now())

    message = payload["message"]
    web.chat_update(
        channel=payload["channel"]["id"],
        ts=message["ts"],
        text=message.get("text", "Antares lead"),
        blocks=_swap_status_block(message.get("blocks", []), new_block),
    )
    log.info("%s -> %s by %s", key[:12], status, user_id)


def main() -> int:
    cfg = load_config()
    if not cfg.slack_app_token or not cfg.slack_bot_token:
        # Exit 0 so launchd's KeepAlive doesn't restart-loop until the token is added.
        log.error("SLACK_APP_TOKEN and SLACK_BOT_TOKEN are required; see .env.example")
        return 0

    web = WebClient(token=cfg.slack_bot_token, ssl=_SSL_CONTEXT)
    leads = LeadsDB()
    client = SocketModeClient(app_token=cfg.slack_app_token, web_client=web)

    def on_request(client: SocketModeClient, req: SocketModeRequest) -> None:
        # Ack first: Slack shows an error on the button if we take > 3s.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type == "interactive" and req.payload.get("type") == "block_actions":
            try:
                handle_click(web, leads, req.payload)
            except Exception:
                log.exception("failed to handle click")

    client.socket_mode_request_listeners.append(on_request)
    client.connect()
    log.info("listening for button clicks")
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
