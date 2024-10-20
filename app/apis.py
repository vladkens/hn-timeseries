from typing import TypedDict

import httpx
from loguru import logger

from . import settings


class TgButton(TypedDict):
    text: str
    url: str


async def call_hn(method: str):
    # https://github.com/HackerNews/API
    url = f"https://hacker-news.firebaseio.com/v0/{method}.json"
    async with httpx.AsyncClient() as client:
        rep = await client.get(url)
        rep.raise_for_status()
        return rep.json()


async def post_tg(chat_id: str, text: str, buttons: list[TgButton] | None = None) -> int:
    # https://core.telegram.org/bots/api#sendmessage
    url = f"https://api.telegram.org/bot{settings.TG_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        msg = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": True,
        }

        if buttons and len(buttons) > 0:
            # inline_keyboard – array of arrays, each array represents a row of buttons
            # this fn expects a single row
            msg["reply_markup"] = {"inline_keyboard": [buttons]}

        rep = await client.post(url, json=msg)
        if not rep.is_success:
            logger.error(f"Failed to post to {chat_id=}: {rep.status_code} - {rep.text}")
            rep.raise_for_status()

        rep = rep.json()
        assert rep["ok"], f"Failed to post to {chat_id=}: {rep}"
        return rep["result"]["message_id"]
