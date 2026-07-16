from __future__ import annotations

import asyncio
import hashlib
import logging
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from camera_agent.configuration import AgentConfig

logger = logging.getLogger(__name__)
AudioPlayer = Callable[[Path, int], None]
PlaybackWaiter = Callable[[float], Awaitable[None]]


class CommandProcessingError(RuntimeError):
    pass


async def command_loop(
    *,
    config: AgentConfig,
    stop_event: asyncio.Event,
) -> None:
    if not config.voice_playback_enabled:
        return

    timeout = httpx.Timeout(config.request_timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        while not stop_event.is_set():
            try:
                processed = await poll_command_once(config=config, client=client)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected command-loop error")
                processed = False

            if not processed:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=config.command_poll_seconds,
                    )
                except TimeoutError:
                    pass


async def poll_command_once(
    *,
    config: AgentConfig,
    client: httpx.AsyncClient,
    player: AudioPlayer | None = None,
    playback_wait: PlaybackWaiter = asyncio.sleep,
) -> bool:
    headers = {"Authorization": f"Bearer {config.camera_credential}"}

    try:
        response = await client.get(
            f"{config.commands_url}/next",
            headers=headers,
        )
    except httpx.RequestError:
        return False

    if response.status_code == 204:
        return False

    if response.status_code != 200:
        return False

    command = _parse_command(response.json())
    command_id = command["id"]
    temporary_path = config.temporary_directory / f"command-{command_id}.mp3"

    try:
        audio = await _download_audio(
            client=client,
            url=f"{config.commands_url}/{command_id}/media",
            headers=headers,
            expected_size=command["media_size_bytes"],
        )

        if len(audio) != command["media_size_bytes"]:
            raise CommandProcessingError("audio_size_mismatch")

        if hashlib.sha256(audio).hexdigest() != command["media_sha256"]:
            raise CommandProcessingError("audio_checksum_mismatch")

        await asyncio.to_thread(temporary_path.write_bytes, audio)
        await _report_result(
            config=config,
            client=client,
            command_id=command_id,
            status="started",
        )
        await asyncio.to_thread(
            player or play_audio,
            temporary_path,
            config.playback_timeout_seconds,
        )
        await playback_wait(command["duration_seconds"])
        await _report_result(
            config=config,
            client=client,
            command_id=command_id,
            status="completed",
        )
    except CommandProcessingError as error:
        await _safe_report_result(
            config=config,
            client=client,
            command_id=command_id,
            status="failed",
            error_code=str(error),
        )
    except Exception:
        await _safe_report_result(
            config=config,
            client=client,
            command_id=command_id,
            status="failed",
            error_code="audio_playback_failed",
        )
    finally:
        await asyncio.to_thread(temporary_path.unlink, missing_ok=True)

    return True


async def _download_audio(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    expected_size: int,
) -> bytes:
    chunks: list[bytes] = []
    downloaded_size = 0

    async with client.stream("GET", url, headers=headers) as response:
        if response.status_code != 200:
            raise CommandProcessingError("audio_download_failed")

        async for chunk in response.aiter_bytes():
            downloaded_size += len(chunk)

            if downloaded_size > expected_size:
                raise CommandProcessingError("audio_size_mismatch")

            chunks.append(chunk)

    return b"".join(chunks)


def play_audio(path: Path, timeout_seconds: int) -> None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed Termux command and argument list
            ["termux-media-player", "play", str(path)],  # noqa: S607
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CommandProcessingError("audio_playback_failed") from None

    if result.returncode != 0:
        raise CommandProcessingError("audio_playback_failed")


def _parse_command(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        command_id = str(UUID(str(payload["id"])))
        command_type = str(payload["command_type"])
        media_size_bytes = int(payload["media_size_bytes"])
        media_sha256 = str(payload["media_sha256"])
        media_mime_type = str(payload["media_mime_type"])
        duration_seconds = int(payload["duration_seconds"])
    except (KeyError, TypeError, ValueError):
        raise CommandProcessingError("invalid_command_response") from None

    if command_type != "play_audio":
        raise CommandProcessingError("unsupported_command")

    if not 0 < media_size_bytes <= 20 * 1024 * 1024:
        raise CommandProcessingError("invalid_command_response")

    if len(media_sha256) != 64 or media_mime_type != "audio/mpeg":
        raise CommandProcessingError("invalid_command_response")

    if not 1 <= duration_seconds <= 300:
        raise CommandProcessingError("invalid_command_response")

    return {
        "id": command_id,
        "media_size_bytes": media_size_bytes,
        "media_sha256": media_sha256,
        "duration_seconds": duration_seconds,
    }


async def _safe_report_result(
    *,
    config: AgentConfig,
    client: httpx.AsyncClient,
    command_id: str,
    status: str,
    error_code: str | None = None,
) -> None:
    try:
        await _report_result(
            config=config,
            client=client,
            command_id=command_id,
            status=status,
            error_code=error_code,
        )
    except Exception:
        logger.warning(
            "command_result_report_failed command_id=%s",
            command_id,
        )


async def _report_result(
    *,
    config: AgentConfig,
    client: httpx.AsyncClient,
    command_id: str,
    status: str,
    error_code: str | None = None,
) -> None:
    payload = {"status": status}

    if error_code is not None:
        payload["error_code"] = error_code

    response = await client.post(
        f"{config.commands_url}/{command_id}/result",
        headers={"Authorization": f"Bearer {config.camera_credential}"},
        json=payload,
    )

    if response.status_code != 200:
        raise CommandProcessingError("command_result_rejected")
