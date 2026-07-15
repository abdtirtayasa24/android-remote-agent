from __future__ import annotations

import asyncio
import logging

from camera_agent.configuration import AgentConfig
from camera_agent.queue import QueueStore

logger = logging.getLogger(__name__)


async def cleanup_loop(
    *,
    config: AgentConfig,
    queue: QueueStore,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            dropped = await asyncio.to_thread(
                queue.enforce_limits,
                maximum_bytes=(config.queue_max_bytes),
                maximum_age_hours=(config.queue_max_age_hours),
            )

            if dropped:
                logger.warning(
                    "Queue cleanup dropped %s scheduled images",
                    dropped,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected queue-cleanup error")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=(config.cleanup_interval_seconds),
            )
        except TimeoutError:
            pass
