from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class TelegramClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramClient:
    bot_token: str
    timeout_seconds: float = 10

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
    ) -> int | None:
        response = await self._post(
            "sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
        )

        return _message_id(response)

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str | None = None,
    ) -> int | None:
        data: dict[str, Any] = {"chat_id": chat_id}

        if caption is not None:
            data["caption"] = caption

        with photo_path.open("rb") as photo:
            response = await self._post(
                "sendPhoto",
                data=data,
                files={"photo": (photo_path.name, photo, "image/jpeg")},
            )

        return _message_id(response)

    async def _post(
        self,
        method: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/{method}",
                    **kwargs,
                )
        except httpx.HTTPError as error:
            raise TelegramClientError(
                f"Telegram API request failed: {type(error).__name__}"
            ) from None

        if response.status_code >= 400:
            raise TelegramClientError(f"Telegram API request failed: {response.status_code}")

        payload = response.json()

        if not payload.get("ok"):
            raise TelegramClientError("Telegram API returned ok=false")

        return payload


def _message_id(payload: dict[str, Any]) -> int | None:
    result = payload.get("result")

    if not isinstance(result, dict):
        return None

    message_id = result.get("message_id")

    if isinstance(message_id, int):
        return message_id

    return None
