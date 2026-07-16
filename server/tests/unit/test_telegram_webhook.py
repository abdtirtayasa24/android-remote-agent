from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient
from pydantic import SecretStr
from telegram import Update
from timelapse.api.main import app
from timelapse.configuration import get_settings


@dataclass
class FakeUpdateQueue:
    updates: list[Update] = field(default_factory=list)

    async def put(self, update: Update) -> None:
        self.updates.append(update)


@dataclass
class FakeTelegramApplication:
    update_queue: FakeUpdateQueue = field(default_factory=FakeUpdateQueue)
    bot: object = field(default_factory=object)


def test_telegram_webhook_rejects_invalid_secret() -> None:
    telegram_application = FakeTelegramApplication()
    app.state.telegram_application = telegram_application
    app.dependency_overrides[get_settings] = lambda: type(
        "WebhookSettings",
        (),
        {"telegram_webhook_secret": SecretStr("expected-secret")},
    )()

    try:
        response = TestClient(app).post(
            "/api/v1/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
            json={"update_id": 123},
        )
    finally:
        app.dependency_overrides.clear()
        del app.state.telegram_application

    assert response.status_code == 403
    assert telegram_application.update_queue.updates == []


def test_telegram_webhook_dispatches_valid_update() -> None:
    telegram_application = FakeTelegramApplication()
    app.state.telegram_application = telegram_application
    app.dependency_overrides[get_settings] = lambda: type(
        "WebhookSettings",
        (),
        {"telegram_webhook_secret": SecretStr("expected-secret")},
    )()

    try:
        response = TestClient(app).post(
            "/api/v1/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "expected-secret"},
            json={"update_id": 123},
        )
    finally:
        app.dependency_overrides.clear()
        del app.state.telegram_application

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert [update.update_id for update in telegram_application.update_queue.updates] == [123]
