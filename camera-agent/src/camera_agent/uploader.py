from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any

import httpx

from camera_agent.configuration import AgentConfig
from camera_agent.queue import QueueItem, QueueStore

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = (
    30,
    60,
    120,
    300,
    900,
    1800,
    3600,
)

_PERMANENT_REJECTION_STATUSES = {
    400,
    401,
    403,
    409,
    413,
    422,
}


def retry_delay_seconds(
    retry_count: int,
    *,
    jitter_factor: float | None = None,
) -> int:
    base_delay = _BACKOFF_SECONDS[
        min(
            retry_count,
            len(_BACKOFF_SECONDS) - 1,
        )
    ]

    factor = jitter_factor if jitter_factor is not None else random.uniform(0.8, 1.2)

    return max(
        1,
        round(base_delay * factor),
    )


async def upload_loop(
    *,
    config: AgentConfig,
    queue: QueueStore,
    stop_event: asyncio.Event,
) -> None:
    timeout = httpx.Timeout(config.request_timeout_seconds)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        while not stop_event.is_set():
            try:
                item = await asyncio.to_thread(queue.claim_due)

                if item is None:
                    await _wait_or_stop(
                        stop_event,
                        config.upload_poll_seconds,
                    )
                    continue

                await upload_one(
                    config=config,
                    queue=queue,
                    client=client,
                    item=item,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected upload-loop error")

                await _wait_or_stop(
                    stop_event,
                    config.upload_poll_seconds,
                )


async def upload_one(
    *,
    config: AgentConfig,
    queue: QueueStore,
    client: httpx.AsyncClient,
    item: QueueItem,
) -> None:
    if not item.file_path.is_file():
        await asyncio.to_thread(
            queue.mark_failed,
            item.capture_id,
            error_code="local_file_missing",
        )
        return

    headers = {
        "Authorization": (f"Bearer {config.camera_credential}"),
    }

    form_data = {
        "capture_id": item.capture_id,
        "captured_at_utc": (item.captured_at_utc),
        "capture_source": (item.capture_source),
        "sha256": item.sha256,
    }

    try:
        with item.file_path.open("rb") as image_file:
            response = await client.post(
                config.image_upload_url,
                headers=headers,
                data=form_data,
                files={
                    "image": (
                        Path(item.file_path).name,
                        image_file,
                        "image/jpeg",
                    )
                },
            )
    except httpx.RequestError:
        await _reschedule(
            queue=queue,
            item=item,
            error_code="upload_network_error",
        )
        return
    except OSError:
        await _reschedule(
            queue=queue,
            item=item,
            error_code="upload_local_file_error",
        )
        return

    if response.status_code in {200, 201}:
        if not _is_confirmed_response(
            response=response,
            capture_id=item.capture_id,
        ):
            await _reschedule(
                queue=queue,
                item=item,
                error_code=("invalid_upload_confirmation"),
            )
            return

        response_data = response.json()

        await asyncio.to_thread(
            queue.record_upload_success,
            response_data["received_at_utc"],
        )

        deleted = await asyncio.to_thread(
            queue.confirm_uploaded,
            item.capture_id,
            item.file_path,
        )

        if deleted:
            logger.info(
                "Upload confirmed and local file removed: %s",
                item.capture_id,
            )
        else:
            logger.warning(
                "Upload confirmed; local deletion deferred: %s",
                item.capture_id,
            )

        return

    if response.status_code in _PERMANENT_REJECTION_STATUSES:
        error_code = f"upload_rejected_{response.status_code}"

        await asyncio.to_thread(
            queue.mark_failed,
            item.capture_id,
            error_code=error_code,
        )

        logger.error(
            "Upload permanently rejected: capture=%s status=%s",
            item.capture_id,
            response.status_code,
        )
        return

    await _reschedule(
        queue=queue,
        item=item,
        error_code=(f"upload_http_{response.status_code}"),
    )


def _is_confirmed_response(
    *,
    response: httpx.Response,
    capture_id: str,
) -> bool:
    try:
        data: dict[str, Any] = response.json()
    except ValueError:
        return False

    return (
        data.get("capture_id") == capture_id
        and data.get("status")
        in {
            "stored",
            "already_stored",
        }
        and isinstance(
            data.get("received_at_utc"),
            str,
        )
    )


async def _reschedule(
    *,
    queue: QueueStore,
    item: QueueItem,
    error_code: str,
) -> None:
    delay = retry_delay_seconds(item.retry_count)

    await asyncio.to_thread(
        queue.reschedule,
        item.capture_id,
        delay_seconds=delay,
        error_code=error_code,
    )

    logger.warning(
        "Upload deferred: capture=%s delay=%ss error=%s",
        item.capture_id,
        delay,
        error_code,
    )


async def _wait_or_stop(
    stop_event: asyncio.Event,
    seconds: float,
) -> None:
    try:
        await asyncio.wait_for(
            stop_event.wait(),
            timeout=seconds,
        )
    except TimeoutError:
        pass
