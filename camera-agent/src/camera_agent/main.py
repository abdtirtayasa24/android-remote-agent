from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import os
import signal
import time
from pathlib import Path
from typing import IO

from camera_agent.capture import (
    CaptureError,
    capture_and_prepare,
)
from camera_agent.cleanup import cleanup_loop
from camera_agent.configuration import (
    ConfigurationError,
    AgentConfig,
    load_config,
)
from camera_agent.heartbeat import heartbeat_loop
from camera_agent.queue import (
    QueueItem,
    QueueStore,
)
from camera_agent.uploader import upload_loop

logger = logging.getLogger(__name__)


async def capture_loop(
    *,
    config: AgentConfig,
    queue: QueueStore,
    stop_event: asyncio.Event,
) -> None:
    next_capture = time.monotonic()

    while not stop_event.is_set():
        delay = next_capture - time.monotonic()

        if delay > 0:
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=delay,
                )
                continue
            except TimeoutError:
                pass

        try:
            captured = await asyncio.to_thread(
                capture_and_prepare,
                config,
            )

            await asyncio.to_thread(
                queue.enqueue,
                QueueItem(
                    capture_id=(captured.capture_id),
                    file_path=(captured.file_path),
                    captured_at_utc=(captured.captured_at_utc),
                    capture_source="scheduled",
                    file_size_bytes=(captured.file_size_bytes),
                    sha256=captured.sha256,
                ),
            )

            await asyncio.to_thread(
                queue.record_capture_success,
                captured.captured_at_utc,
            )

            logger.info(
                "Capture queued: capture=%s bytes=%s",
                captured.capture_id,
                captured.file_size_bytes,
            )
        except CaptureError as exc:
            error_code = str(exc)[:128]

            await asyncio.to_thread(
                queue.record_capture_failure,
                error_code,
            )

            logger.error(
                "Capture failed: %s",
                error_code,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.to_thread(
                queue.record_capture_failure,
                "capture_unexpected_error",
            )
            logger.exception("Unexpected capture-loop error")

        next_capture += config.capture_interval_seconds
        current_time = time.monotonic()

        if next_capture <= current_time:
            missed_intervals = (
                int((current_time - next_capture) // config.capture_interval_seconds)
                + 1
            )

            next_capture += missed_intervals * config.capture_interval_seconds


async def run_agent(
    config: AgentConfig,
) -> None:
    config.prepare_directories()

    lock_handle = acquire_process_lock(config.lock_file_path)

    queue = QueueStore(config.queue_database_path)
    queue.initialize()

    recovered = queue.recover_interrupted_uploads()
    finalized = queue.finalize_uploaded_files()

    if recovered:
        logger.warning(
            "Recovered %s interrupted uploads",
            recovered,
        )

    if finalized:
        logger.info(
            "Finalized %s confirmed local deletions",
            finalized,
        )

    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()

    for signal_number in (
        signal.SIGINT,
        signal.SIGTERM,
    ):
        event_loop.add_signal_handler(
            signal_number,
            stop_event.set,
        )

    process_started = time.monotonic()

    tasks = [
        asyncio.create_task(
            capture_loop(
                config=config,
                queue=queue,
                stop_event=stop_event,
            ),
            name="capture-loop",
        ),
        asyncio.create_task(
            upload_loop(
                config=config,
                queue=queue,
                stop_event=stop_event,
            ),
            name="upload-loop",
        ),
        asyncio.create_task(
            cleanup_loop(
                config=config,
                queue=queue,
                stop_event=stop_event,
            ),
            name="cleanup-loop",
        ),
        asyncio.create_task(
            heartbeat_loop(
                config=config,
                queue=queue,
                stop_event=stop_event,
                process_started_monotonic=(process_started),
            ),
            name="heartbeat-loop",
        ),
    ]

    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        lock_handle.close()


def acquire_process_lock(
    path: Path,
) -> IO[str]:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    handle = path.open(
        "a+",
        encoding="utf-8",
    )

    try:
        fcntl.flock(
            handle.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except BlockingIOError as exc:
        handle.close()

        raise RuntimeError("Another camera-agent process is already running") from exc

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()

    return handle


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s %(levelname)s %(name)s %(message)s"),
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=("Android time-lapse camera agent"))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("~/timelapse/config.json").expanduser(),
    )

    return parser.parse_args()


def main() -> None:
    configure_logging()
    arguments = parse_arguments()

    try:
        config = load_config(arguments.config)
        asyncio.run(run_agent(config))
    except (
        ConfigurationError,
        RuntimeError,
    ) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        raise SystemExit(0) from None


if __name__ == "__main__":
    main()
