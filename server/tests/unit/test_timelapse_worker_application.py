from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from timelapse.services.storage_pressure import StoragePressureState
from timelapse.workers import application as worker_application

BOT_TOKEN = "123456:test-token"  # noqa: S105 - fake token for worker tests


async def test_daily_timelapse_without_telegram_only_runs_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        daily_timelapse_enabled=True,
        telegram_bot_token=None,
        telegram_admin_user_id=None,
        timelapses_directory=tmp_path / "timelapses",
        daily_timelapse_frame_rate=24,
        daily_timelapse_send_hour_jakarta=0,
        daily_timelapse_send_minute_jakarta=10,
    )
    cleanup_senders: list[object] = []

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def unexpected_job_creation(**kwargs) -> int:
        raise AssertionError("jobs must not be created without Telegram")

    async def record_cleanup(**kwargs) -> int:
        cleanup_senders.append(kwargs["sender"])
        return 1

    monkeypatch.setattr(worker_application, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker_application,
        "get_storage_pressure_state",
        lambda **kwargs: StoragePressureState.NORMAL,
    )
    monkeypatch.setattr(worker_application, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        worker_application,
        "create_due_video_jobs_once",
        unexpected_job_creation,
    )
    monkeypatch.setattr(
        worker_application,
        "process_due_video_jobs_once",
        record_cleanup,
    )

    processed_count = await worker_application.run_daily_timelapse_once()

    assert processed_count == 1
    assert cleanup_senders == [None]


async def test_daily_timelapse_without_recipients_does_not_create_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        daily_timelapse_enabled=True,
        telegram_bot_token=SecretStr(BOT_TOKEN),
        telegram_admin_user_id=None,
        timelapses_directory=tmp_path / "timelapses",
        daily_timelapse_frame_rate=24,
        daily_timelapse_send_hour_jakarta=0,
        daily_timelapse_send_minute_jakarta=10,
    )
    cleanup_senders: list[object] = []

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def no_recipients(**kwargs):
        return ()

    async def unexpected_job_creation(**kwargs) -> int:
        raise AssertionError("jobs must not be created without recipients")

    async def record_cleanup(**kwargs) -> int:
        cleanup_senders.append(kwargs["sender"])
        return 1

    monkeypatch.setattr(worker_application, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_application, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        worker_application,
        "get_storage_pressure_state",
        lambda **kwargs: StoragePressureState.NORMAL,
    )
    monkeypatch.setattr(
        worker_application,
        "load_telegram_recipient_chat_ids",
        no_recipients,
    )
    monkeypatch.setattr(
        worker_application,
        "create_due_video_jobs_once",
        unexpected_job_creation,
    )
    monkeypatch.setattr(
        worker_application,
        "process_due_video_jobs_once",
        record_cleanup,
    )

    processed_count = await worker_application.run_daily_timelapse_once()

    assert processed_count == 1
    assert cleanup_senders[0] is not None


async def test_daily_timelapse_under_storage_pressure_does_not_create_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        daily_timelapse_enabled=True,
        telegram_bot_token=SecretStr(BOT_TOKEN),
        telegram_admin_user_id=123,
        timelapses_directory=tmp_path / "timelapses",
        daily_timelapse_frame_rate=24,
        daily_timelapse_send_hour_jakarta=0,
        daily_timelapse_send_minute_jakarta=10,
    )
    observed_pressure: list[StoragePressureState] = []

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def recipients(**kwargs):
        return (123,)

    async def unexpected_job_creation(**kwargs) -> int:
        raise AssertionError("jobs must not be created under storage pressure")

    async def record_processing(**kwargs) -> int:
        observed_pressure.append(kwargs["storage_pressure_state"])
        return 0

    monkeypatch.setattr(worker_application, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_application, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        worker_application,
        "get_storage_pressure_state",
        lambda **kwargs: StoragePressureState.SEVERE,
    )
    monkeypatch.setattr(
        worker_application,
        "load_telegram_recipient_chat_ids",
        recipients,
    )
    monkeypatch.setattr(
        worker_application,
        "create_due_video_jobs_once",
        unexpected_job_creation,
    )
    monkeypatch.setattr(
        worker_application,
        "process_due_video_jobs_once",
        record_processing,
    )

    processed_count = await worker_application.run_daily_timelapse_once()

    assert processed_count == 0
    assert observed_pressure == [StoragePressureState.SEVERE]
