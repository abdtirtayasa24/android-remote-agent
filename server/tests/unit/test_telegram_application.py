from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from telegram.ext import MessageHandler
from timelapse.api import main as api_main
from timelapse.bot.application import (
    build_application,
    start_webhook_application,
    stop_webhook_application,
)

BOT_TOKEN = "123456:test-token"  # noqa: S105 - fake token for redaction tests
WEBHOOK_SECRET = "webhook-secret"  # noqa: S105 - fake secret for lifecycle tests


@dataclass
class FakeBot:
    webhook_requests: list[dict[str, object]] = field(default_factory=list)
    failure: Exception | None = None
    result: bool = True

    async def set_webhook(self, **kwargs: object) -> bool:
        self.webhook_requests.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.result


@dataclass
class FakeApplication:
    bot: FakeBot
    initialized: bool = False
    running: bool = False
    shutdown_called: bool = False

    async def initialize(self) -> None:
        self.initialized = True

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def shutdown(self) -> None:
        self.shutdown_called = True
        self.initialized = False


def test_telegram_application_registers_voice_note_handler() -> None:
    application = build_application(bot_token=BOT_TOKEN)

    assert any(
        isinstance(handler, MessageHandler)
        for handlers in application.handlers.values()
        for handler in handlers
    )


async def test_webhook_application_starts_and_registers_webhook() -> None:
    application = FakeApplication(bot=FakeBot())

    await start_webhook_application(
        application=application,
        webhook_url="https://camera.example.com/api/v1/telegram/webhook",
        webhook_secret=WEBHOOK_SECRET,
    )

    assert application.initialized is True
    assert application.running is True
    assert application.bot.webhook_requests == [
        {
            "url": "https://camera.example.com/api/v1/telegram/webhook",
            "secret_token": "webhook-secret",
            "allowed_updates": [
                "message",
            ],
            "connect_timeout": 10,
            "read_timeout": 10,
            "write_timeout": 10,
            "pool_timeout": 10,
        }
    ]

    await stop_webhook_application(application=application)

    assert application.running is False
    assert application.initialized is False
    assert application.shutdown_called is True


async def test_false_webhook_registration_result_stops_api_dependency() -> None:
    application = FakeApplication(bot=FakeBot(result=False))

    with pytest.raises(RuntimeError, match="telegram_webhook_setup_failed"):
        await start_webhook_application(
            application=application,
            webhook_url="https://camera.example.com/api/v1/telegram/webhook",
            webhook_secret=WEBHOOK_SECRET,
        )

    assert application.running is False
    assert application.initialized is False
    assert application.shutdown_called is True


async def test_webhook_registration_failure_is_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = FakeApplication(
        bot=FakeBot(failure=RuntimeError(f"failed request for bot{BOT_TOKEN}")),
    )

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(RuntimeError, match="telegram_webhook_setup_failed") as error,
    ):
        await start_webhook_application(
            application=application,
            webhook_url="https://camera.example.com/api/v1/telegram/webhook",
            webhook_secret=WEBHOOK_SECRET,
        )

    formatted_error = "".join(traceback.format_exception(error.value))
    assert BOT_TOKEN not in formatted_error
    assert BOT_TOKEN not in caplog.text
    assert application.running is False
    assert application.initialized is False
    assert application.shutdown_called is True


def test_api_lifespan_starts_and_stops_telegram_webhook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    application = FakeApplication(bot=FakeBot())
    settings = type(
        "WebhookSettings",
        (),
        {
            "storage_root": tmp_path,
            "telegram_bot_token": SecretStr(BOT_TOKEN),
            "telegram_webhook_secret": SecretStr("webhook-secret"),
            "public_domain": "camera.example.com",
        },
    )()
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "ensure_storage_layout", lambda configured: None)
    monkeypatch.setattr(
        api_main,
        "build_application",
        lambda **kwargs: application,
        raising=False,
    )

    with TestClient(api_main.app):
        assert application.running is True

    assert application.shutdown_called is True
    assert application.bot.webhook_requests[0]["url"] == (
        "https://camera.example.com/api/v1/telegram/webhook"
    )


def test_api_startup_fails_when_webhook_registration_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    application = FakeApplication(
        bot=FakeBot(failure=RuntimeError("telegram unavailable")),
    )
    settings = type(
        "WebhookSettings",
        (),
        {
            "storage_root": tmp_path,
            "telegram_bot_token": SecretStr(BOT_TOKEN),
            "telegram_webhook_secret": SecretStr("webhook-secret"),
            "public_domain": "camera.example.com",
        },
    )()
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "ensure_storage_layout", lambda configured: None)
    monkeypatch.setattr(
        api_main,
        "build_application",
        lambda **kwargs: application,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="telegram_webhook_setup_failed"):
        with TestClient(api_main.app):
            pass
