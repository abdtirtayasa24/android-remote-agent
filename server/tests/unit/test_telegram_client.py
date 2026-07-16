from __future__ import annotations

import traceback

import httpx
import pytest
from timelapse.services.telegram_client import TelegramClient, TelegramClientError


class FailingAsyncClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> FailingAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        pass

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        raise httpx.ConnectError(f"failed to connect to {url}", request=request)


async def test_telegram_client_errors_do_not_expose_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", FailingAsyncClient)
    token = "123456:secret-token"  # noqa: S105 - fake token used to test redaction
    client = TelegramClient(bot_token=token)

    with pytest.raises(TelegramClientError) as error:
        await client.send_message(chat_id=123456, text="hello")

    formatted_error = "".join(traceback.format_exception(error.value))

    assert token not in str(error.value)
    assert "secret-token" not in repr(error.value)
    assert token not in formatted_error
    assert error.value.__suppress_context__ is True
