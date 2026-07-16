from __future__ import annotations

import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from timelapse.services.telegram_client import TelegramClient, TelegramClientError


class RecordingAsyncClient:
    requests: list[tuple[str, dict[str, object]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> RecordingAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        pass

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.requests.append((url, kwargs))
        result = (
            {"file_path": "voice/file.oga"} if url.endswith("/getFile") else {"message_id": 42}
        )
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"ok": True, "result": result},
        )

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kwargs: object):
        self.requests.append((url, kwargs))
        yield httpx.Response(
            200,
            request=httpx.Request(method, url),
            content=b"telegram-voice",
        )


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


async def test_telegram_client_sends_mp4_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    RecordingAsyncClient.requests = []
    monkeypatch.setattr(httpx, "AsyncClient", RecordingAsyncClient)
    video_path = tmp_path / "daily.mp4"
    video_path.write_bytes(b"mp4")
    client = TelegramClient(bot_token="123456:test-token")  # noqa: S106

    message_id = await client.send_video(
        chat_id=123456,
        video_path=video_path,
        caption="Daily video",
    )

    assert message_id == 42
    url, kwargs = RecordingAsyncClient.requests[0]
    assert url.endswith("/sendVideo")
    assert kwargs["data"] == {"chat_id": 123456, "caption": "Daily video"}
    assert kwargs["files"]["video"][0] == "daily.mp4"
    assert kwargs["files"]["video"][2] == "video/mp4"


async def test_telegram_client_downloads_file_without_exposing_remote_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    RecordingAsyncClient.requests = []
    monkeypatch.setattr(httpx, "AsyncClient", RecordingAsyncClient)
    destination = tmp_path / "voice.oga"
    client = TelegramClient(bot_token="123456:test-token")  # noqa: S106

    await client.download_file(file_id="voice-id", destination=destination)

    assert destination.read_bytes() == b"telegram-voice"
    assert RecordingAsyncClient.requests[0][0].endswith("/getFile")
    assert RecordingAsyncClient.requests[0][1]["json"] == {"file_id": "voice-id"}
    assert RecordingAsyncClient.requests[1][0].endswith("/voice/file.oga")


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
