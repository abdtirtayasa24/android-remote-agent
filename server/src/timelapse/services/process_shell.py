from __future__ import annotations

import asyncio
import logging
import signal

from timelapse.configuration import get_settings
from timelapse.database import close_database, database_is_ready
from timelapse.logging import configure_logging

LOGGER = logging.getLogger(__name__)


async def run_process_shell(service_name: str) -> None:
    settings = get_settings()
    configure_logging(service_name, settings.log_level)

    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        event_loop.add_signal_handler(signal_number, stop_event.set)

    database_ready_was_logged = False

    LOGGER.info("process_started milestone=2 mode=foundation_shell")

    try:
        while not stop_event.is_set():
            database_ready = await database_is_ready()

            if database_ready and not database_ready_was_logged:
                LOGGER.info("database_ready")
                database_ready_was_logged = True
            elif not database_ready:
                LOGGER.warning("database_unavailable")
                database_ready_was_logged = False

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=30)
            except TimeoutError:
                continue
    finally:
        await close_database()
        LOGGER.info("process_stopped")
