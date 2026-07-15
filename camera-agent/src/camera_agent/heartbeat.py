from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from camera_agent.configuration import AgentConfig
from camera_agent.queue import QueueStore

logger = logging.getLogger(__name__)

UTC = timezone.utc


async def heartbeat_loop(
    *,
    config: AgentConfig,
    queue: QueueStore,
    stop_event: asyncio.Event,
    process_started_monotonic: float,
) -> None:
    timeout = httpx.Timeout(config.request_timeout_seconds)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        while not stop_event.is_set():
            try:
                await send_heartbeat(
                    config=config,
                    queue=queue,
                    client=client,
                    process_started_monotonic=(process_started_monotonic),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected heartbeat-loop error")

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=(config.heartbeat_interval_seconds),
                )
            except TimeoutError:
                pass


async def send_heartbeat(
    *,
    config: AgentConfig,
    queue: QueueStore,
    client: httpx.AsyncClient,
    process_started_monotonic: float,
) -> None:
    metrics = await asyncio.to_thread(queue.metrics)
    runtime = await asyncio.to_thread(queue.runtime_state)
    battery = await asyncio.to_thread(_read_battery_status)
    available_storage = await asyncio.to_thread(
        _available_storage_bytes,
        config.root_directory,
    )

    last_error_code = runtime.last_error_code

    if runtime.consecutive_heartbeat_failures:
        last_error_code = (
            "heartbeat_recovered_after_"
            f"{runtime.consecutive_heartbeat_failures}"
            "_failures"
        )

    payload = {
        "agent_version": config.agent_version,
        "sent_at_utc": utc_now_text(),
        "uptime_seconds": max(
            0,
            int(time.monotonic() - process_started_monotonic),
        ),
        "battery_percent": battery.get("percentage"),
        "battery_status": battery.get("status"),
        "battery_temperature_c": (battery.get("temperature")),
        "available_storage_bytes": (available_storage),
        "pending_image_count": (metrics.pending_image_count),
        "pending_image_bytes": (metrics.pending_image_bytes),
        "oldest_pending_at_utc": (metrics.oldest_pending_at_utc),
        "last_capture_at_utc": (runtime.last_capture_at_utc),
        "last_upload_at_utc": (runtime.last_upload_at_utc),
        "dropped_image_count": (runtime.dropped_image_count),
        "consecutive_capture_failures": (runtime.consecutive_capture_failures),
        "last_error_code": last_error_code,
    }

    try:
        response = await client.post(
            config.heartbeat_url,
            headers={"Authorization": (f"Bearer {config.camera_credential}")},
            json=payload,
        )
    except httpx.RequestError:
        await asyncio.to_thread(
            queue.record_heartbeat_failure,
            "heartbeat_network_error",
        )
        return

    if response.status_code != 200:
        await asyncio.to_thread(
            queue.record_heartbeat_failure,
            (f"heartbeat_http_{response.status_code}"),
        )
        return

    try:
        response_data: dict[str, Any] = response.json()
    except ValueError:
        await asyncio.to_thread(
            queue.record_heartbeat_failure,
            "heartbeat_invalid_response",
        )
        return

    if not isinstance(
        response_data.get("configuration_version"),
        int,
    ):
        await asyncio.to_thread(
            queue.record_heartbeat_failure,
            "heartbeat_invalid_response",
        )
        return

    await asyncio.to_thread(
        queue.acknowledge_dropped_images,
        runtime.dropped_image_count,
    )
    await asyncio.to_thread(queue.record_heartbeat_success)

    if not response_data.get(
        "camera_enabled",
        True,
    ):
        logger.warning("Server reports that the camera is disabled")

    logger.info(
        "Heartbeat accepted: configuration_version=%s",
        response_data["configuration_version"],
    )


def _read_battery_status() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["termux-battery-status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        return {}

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    percentage = _optional_int(data.get("percentage"))
    temperature = _optional_float(data.get("temperature"))
    status = data.get("status")

    return {
        "percentage": percentage,
        "temperature": temperature,
        "status": (str(status)[:32] if status is not None else None),
    }


def _available_storage_bytes(path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    if not 0 <= parsed <= 100:
        return None

    return parsed


def _optional_float(
    value: Any,
) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
