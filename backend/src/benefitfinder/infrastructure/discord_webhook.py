"""Send a small F08 status message without logging the webhook or response body."""

import json
import re
from http.client import HTTPSConnection, HTTPException
from urllib.parse import parse_qs, urlencode, urlsplit


class DiscordWebhookError(ValueError):
    """Fixed notification error, with no URL, token, or response attached."""


class DiscordWebhook:
    def __init__(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
            query = parse_qs(
                parsed.query,
                strict_parsing=True,
                keep_blank_values=True,
            )
            valid = (
                parsed.scheme == "https"
                and parsed.netloc == "discord.com"
                and not parsed.fragment
                and re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+/?", parsed.path)
                and set(query) <= {"wait", "thread_id"}
                and all(len(values) == 1 for values in query.values())
                and ("thread_id" not in query or query["thread_id"][0].isdigit())
            )
            if not valid:
                raise ValueError
        except ValueError:
            raise DiscordWebhookError("discord_webhook_invalid") from None
        parameters = {"wait": "true"}
        if "thread_id" in query:
            parameters["thread_id"] = query["thread_id"][0]
        self._path = parsed.path + "?" + urlencode(parameters)

    def send(self, message: str) -> None:
        if not message or len(message) > 2000:
            raise DiscordWebhookError("discord_message_invalid")
        payload = json.dumps(
            {"content": message, "allowed_mentions": {"parse": []}}, ensure_ascii=False
        ).encode("utf-8")
        connection = HTTPSConnection("discord.com", timeout=10)
        try:
            connection.request(
                "POST",
                self._path,
                payload,
                {"Content-Type": "application/json", "User-Agent": "benefitfinder-ai/0.1"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise DiscordWebhookError("discord_notification_failed")
        except (OSError, HTTPException):
            raise DiscordWebhookError("discord_notification_failed") from None
        finally:
            connection.close()
