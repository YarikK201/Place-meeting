"""Minimal MAX bot for local, supervised development testing.

Uses Python standard library and system certificate trust; never disables TLS checks.
"""
import json
import logging
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://platform-api2.max.ru"
WELCOME = (
    "Привет! Это «Место встречи» — сервис для поиска школьных "
    "мероприятий, проектов и единомышленников. Пока мы готовим первую версию!"
)
LOG = logging.getLogger("place_meeting")


class MaxAPIError(Exception):
    pass


class MaxClient:
    def __init__(self, token: str):
        if not token.strip():
            raise ValueError("MAX_BOT_TOKEN is empty")
        self.token = token

    def request(self, method: str, endpoint: str, *, params=None, payload=None, timeout=40):
        url = API_URL + endpoint
        if params:
            url += "?" + urlencode(params)
        headers = {"Authorization": self.token, "Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            # Never log headers or response bodies that could contain sensitive data.
            raise MaxAPIError(f"MAX API HTTP {exc.code} on {endpoint}") from None
        except (URLError, TimeoutError) as exc:
            raise MaxAPIError(f"MAX API connection failed on {endpoint}: {type(exc).__name__}") from None

    def me(self):
        return self.request("GET", "/me")

    def subscriptions(self):
        return self.request("GET", "/subscriptions")

    def updates(self, marker=None):
        params = {"timeout": 30, "limit": 100}
        if marker is not None:
            params["marker"] = marker
        return self.request("GET", "/updates", params=params, timeout=40)

    def send_to_user(self, user_id: int, text: str):
        return self.request("POST", "/messages", params={"user_id": user_id}, payload={"text": text})


def reply_target(update):
    """Only reply to a private /start message; ignore groups and other events."""
    if update.get("update_type") != "message_created":
        return None
    message = update.get("message") or {}
    body = message.get("body") or {}
    if (body.get("text") or "").strip().split(maxsplit=1)[0:1] != ["/start"]:
        return None
    recipient = message.get("recipient") or {}
    sender = message.get("sender") or {}
    # In private dialogs recipient.chat_type is 'dialog'.
    if recipient.get("chat_type") != "dialog":
        return None
    return sender.get("user_id")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token = os.environ.get("MAX_BOT_TOKEN", "")
    if not token:
        raise SystemExit("Set MAX_BOT_TOKEN as an environment variable (never commit it).")
    client = MaxClient(token)
    info = client.me()
    LOG.info("Authenticated MAX bot: user_id=%s", info.get("user_id"))

    if os.environ.get("MAX_BOT_RUN") != "1":
        LOG.info("Read-only verification complete. Set MAX_BOT_RUN=1 to process /start messages.")
        return

    subscriptions = client.subscriptions()
    if subscriptions.get("subscriptions"):
        raise SystemExit("Webhook subscription exists: polling cannot be used concurrently.")

    LOG.info("Starting local Long Polling test mode. Stop with Ctrl+C.")
    marker = None
    while True:
        try:
            batch = client.updates(marker)
            for update in batch.get("updates", []):
                user_id = reply_target(update)
                if user_id is not None:
                    client.send_to_user(user_id, WELCOME)
            marker = batch.get("marker", marker)
        except MaxAPIError as exc:
            LOG.warning("%s; retrying in 5 seconds", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
