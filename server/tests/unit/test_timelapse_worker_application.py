from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from timelapse.services.storage_pressure import StoragePressureState
from timelapse.workers import application as worker_application

BOT_TOKEN = "123456:test-token"  # noqa: S105 - fake token for worker tests


async def test_voice_note_preparation_uses_worker_telegram_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        voice_playback_enabled=True,
        telegram_bot_token=SecretStr(BOT_TOKEN),
        audio_commands_directory=tmp_path / "audio-commands",
        voice_playback_max_file_bytes=1024,
    )
    sessions: list[object] = []

    downloads: list[dict[str, object]] = []
    messages: list[dict[str, object]] = []

    async def download_file(**kwargs) -> None:
        downloads.append(kwargs)

    async def send_message(**kwargs) -> None:
        messages.append(kwargs)

    client = SimpleNamespace(
        download_file=download_file,
        send_message=send_message,
    )

    @asynccontextmanager
    async def fake_session_scope():
        session = object()
        sessions.append(session)
        yield session

    async def prepare_commands(**kwargs) -> int:
        assert kwargs["session"] is sessions[0]
        await kwargs["download_voice"]("voice-id", tmp_path / "voice.oga")
        await kwargs["notify_failure"](123, "safe message")
        assert kwargs["maximum_file_bytes"] == 1024
        return 1

    monkeypatch.setattr(worker_application, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_application, "session_scope", fake_session_scope)
    monkeypatch.setattr(worker_application, "TelegramClient", lambda **kwargs: client)
    monkeypatch.setattr(
        worker_application,
        "prepare_voice_note_commands_once",
        prepare_commands,
        raising=False,
    )

    assert await worker_application.run_voice_note_preparation_once() == 1
    assert downloads == [
        {
            "file_id": "voice-id",
            "destination": tmp_path / "voice.oga",
            "maximum_bytes": 1024,
        }
    ]
    assert messages == [{"chat_id": 123, "text": "safe message"}]


async def test_camera_command_expiry_runs_in_database_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[object] = []

    @asynccontextmanager
    async def fake_session_scope():
        session = object()
        sessions.append(session)
        yield session

    async def expire_commands(**kwargs) -> int:
        assert kwargs["session"] is sessions[0]
        return 2

    monkeypatch.setattr(worker_application, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        worker_application,
        "expire_camera_commands_once",
        expire_commands,
        raising=False,
    )

    assert await worker_application.run_camera_command_expiry_once() == 2


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
