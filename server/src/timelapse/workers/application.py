from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from timelapse.configuration import get_settings
from timelapse.database import close_database, session_scope
from timelapse.logging import configure_logging
from timelapse.services.export_worker import process_due_export_jobs_once
from timelapse.services.health import evaluate_all_cameras_once
from timelapse.services.heartbeat_aggregation import aggregate_due_heartbeats_once
from timelapse.services.motion_worker import process_due_motion_analyses_once
from timelapse.services.reconciliation import process_reconciliation_once
from timelapse.services.retention import (
    process_emergency_cleanup_once,
    process_retention_once,
)
from timelapse.services.storage_pressure import (
    StoragePressureState,
    get_storage_pressure_state,
)
from timelapse.services.telegram_client import TelegramClient
from timelapse.services.telegram_recipients import load_telegram_recipient_chat_ids
from timelapse.services.timelapse_video_requests import create_due_video_jobs_once
from timelapse.services.timelapse_video_worker import process_due_video_jobs_once

LOGGER = logging.getLogger(__name__)


async def worker_loop(
    *,
    stop_event: asyncio.Event,
    interval_seconds: int,
    operation: Callable[[], Awaitable[int]],
    operation_name: str,
) -> None:
    while not stop_event.is_set():
        try:
            processed_count = await operation()
            LOGGER.info(
                "worker_operation_completed operation=%s processed=%s",
                operation_name,
                processed_count,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "worker_operation_failed operation=%s",
                operation_name,
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


async def run_health_evaluation_once() -> int:
    settings = get_settings()
    sender = None

    if settings.telegram_bot_token is not None:
        sender = TelegramClient(
            bot_token=settings.telegram_bot_token.get_secret_value(),
        )

    async with session_scope() as session:
        return await evaluate_all_cameras_once(
            session=session,
            sender=sender,
            now=datetime.now(UTC),
            admin_user_id=settings.telegram_admin_user_id,
        )


async def run_heartbeat_aggregation_once() -> int:
    async with session_scope() as session:
        return await aggregate_due_heartbeats_once(
            session=session,
            now=datetime.now(UTC),
        )


async def run_motion_analysis_once() -> int:
    settings = get_settings()
    sender = None

    if settings.telegram_bot_token is not None:
        sender = TelegramClient(
            bot_token=settings.telegram_bot_token.get_secret_value(),
        )

    async with session_scope() as session:
        return await process_due_motion_analyses_once(
            session=session,
            now=datetime.now(UTC),
            sender=sender,
            admin_user_id=settings.telegram_admin_user_id,
        )


async def run_export_jobs_once() -> int:
    settings = get_settings()
    sender = None

    if settings.telegram_bot_token is not None:
        sender = TelegramClient(
            bot_token=settings.telegram_bot_token.get_secret_value(),
        )

    async with session_scope() as session:
        return await process_due_export_jobs_once(
            session=session,
            storage_root=settings.exports_directory,
            sender=sender,
            now=datetime.now(UTC),
        )


async def run_daily_timelapse_once() -> int:
    settings = get_settings()

    if not settings.daily_timelapse_enabled:
        return 0

    sender = None

    if settings.telegram_bot_token is not None:
        sender = TelegramClient(
            bot_token=settings.telegram_bot_token.get_secret_value(),
            timeout_seconds=120,
        )

    now = datetime.now(UTC)
    storage_pressure_state = get_storage_pressure_state(settings=settings)

    async with session_scope() as session:
        created_count = 0
        recipient_chat_ids = ()

        if sender is not None:
            recipient_chat_ids = await load_telegram_recipient_chat_ids(
                session=session,
                admin_user_id=settings.telegram_admin_user_id,
            )

        if (
            sender is not None
            and recipient_chat_ids
            and storage_pressure_state == StoragePressureState.NORMAL
        ):
            created_count = await create_due_video_jobs_once(
                session=session,
                now=now,
                recipient_chat_ids=recipient_chat_ids,
                send_hour_jakarta=settings.daily_timelapse_send_hour_jakarta,
                send_minute_jakarta=settings.daily_timelapse_send_minute_jakarta,
            )

        processed_count = await process_due_video_jobs_once(
            session=session,
            output_directory=settings.timelapses_directory,
            sender=sender,
            admin_user_id=settings.telegram_admin_user_id,
            now=now,
            frame_rate=settings.daily_timelapse_frame_rate,
            storage_pressure_state=storage_pressure_state,
        )
        return created_count + processed_count


async def run_retention_once() -> int:
    settings = get_settings()
    now = datetime.now(UTC)

    async with session_scope() as session:
        deleted_count = await process_retention_once(
            session=session,
            now=now,
        )
        emergency_deleted_count = await process_emergency_cleanup_once(
            session=session,
            now=now,
            pressure_state=lambda: get_storage_pressure_state(settings=settings),
        )
        return deleted_count + emergency_deleted_count


async def run_reconciliation_once() -> int:
    settings = get_settings()

    async with session_scope() as session:
        result = await process_reconciliation_once(
            session=session,
            storage_root=settings.storage_root,
            now=datetime.now(UTC),
        )

    return (
        result.missing_files
        + result.orphaned_files
        + result.mismatched_files
        + result.stale_staging_rows
        + result.stale_temp_files
        + result.stale_export_files
    )


async def run_worker() -> None:
    settings = get_settings()
    configure_logging("worker", settings.log_level)

    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        event_loop.add_signal_handler(signal_number, stop_event.set)

    LOGGER.info("process_started service=worker")

    tasks = [
        asyncio.create_task(
            worker_loop(
                stop_event=stop_event,
                interval_seconds=settings.health_worker_interval_seconds,
                operation=run_health_evaluation_once,
                operation_name="health_evaluation",
            ),
            name="health-evaluation-loop",
        ),
        asyncio.create_task(
            worker_loop(
                stop_event=stop_event,
                interval_seconds=settings.heartbeat_aggregation_interval_seconds,
                operation=run_heartbeat_aggregation_once,
                operation_name="heartbeat_aggregation",
            ),
            name="heartbeat-aggregation-loop",
        ),
        asyncio.create_task(
            worker_loop(
                stop_event=stop_event,
                interval_seconds=settings.motion_worker_interval_seconds,
                operation=run_motion_analysis_once,
                operation_name="motion_analysis",
            ),
            name="motion-analysis-loop",
        ),
        asyncio.create_task(
            worker_loop(
                stop_event=stop_event,
                interval_seconds=settings.heartbeat_aggregation_interval_seconds,
                operation=run_export_jobs_once,
                operation_name="export_jobs",
            ),
            name="export-jobs-loop",
        ),
        asyncio.create_task(
            worker_loop(
                stop_event=stop_event,
                interval_seconds=settings.daily_timelapse_worker_interval_seconds,
                operation=run_daily_timelapse_once,
                operation_name="daily_timelapse",
            ),
            name="daily-timelapse-loop",
        ),
        asyncio.create_task(
            worker_loop(
                stop_event=stop_event,
                interval_seconds=settings.retention_worker_interval_seconds,
                operation=run_retention_once,
                operation_name="retention",
            ),
            name="retention-loop",
        ),
        asyncio.create_task(
            worker_loop(
                stop_event=stop_event,
                interval_seconds=settings.reconciliation_worker_interval_seconds,
                operation=run_reconciliation_once,
                operation_name="reconciliation",
            ),
            name="reconciliation-loop",
        ),
    ]

    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        await close_database()
        LOGGER.info("process_stopped service=worker")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
