from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import TextIO

from camera_agent.capture import capture_frame
from camera_agent.configuration import AgentConfig, ConfigurationError

LOGGER = logging.getLogger("camera_agent")
DEFAULT_CONFIG_PATH = Path.home() / "timelapse" / "config.json"


class AlreadyRunningError(RuntimeError):
    """Raised when another agent process already owns the runtime lock."""


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: TextIO | None = None

    def __enter__(self) -> SingleInstanceLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise AlreadyRunningError("another camera-agent process is already running") from exc
        
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        self._file = lock_file
        return self
    
    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
    ) -> None:
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Android time-lapse camera validator")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    run_mode = parser.add_mutually_exclusive_group()
    run_mode.add_argument("--once", action="store_true", help="capture one image and exit")
    run_mode.add_argument("--count", type=int, help="capture a finite number of scheduled images")

    parser.add_argument(
        "--camera-id",
        type=int,
        help="temporarily override camera_id for hardware selection testing",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after the first failed capture",
    )
    return parser.parse_args(argv)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logging.Formatter.converter = time.gmtime


def run_capture_schedule(
        config: AgentConfig,
        *,
        capture_count: int | None,
        fail_fast: bool,
) -> int:
    next_capture_deadline = time.monotonic()
    attempts = 0
    failures = 0

    while capture_count is None or attempts < capture_count:
        delay = next_capture_deadline - time.monotonic()
        if delay > 0:
            time.sleep(delay)

        schedule_delay_seconds = max(0.0, time.monotonic() - next_capture_deadline)
        attempts += 1

        try:
            result = capture_frame(config)
            LOGGER.info(
                "capture_stored capture_id=%s captured_at_utc=%s "
                "path=%s dimensions=%sx%s size_bytes=%s sha256=%s "
                "schedule_delay_seconds=%.3f",
                result.capture_id,
                result.captured_at_utc.isoformat().replace("+00:00", "Z"),
                result.file_path,
                result.width_pixels,
                result.height_pixels,
                result.file_size_bytes,
                result.sha256,
                schedule_delay_seconds,
            )
        except Exception:
            failures += 1
            LOGGER.exception(
                "capture_failed attempt=%s schedule_delay_seconds=%.3f",
                attempts,
                schedule_delay_seconds,
            )
            if fail_fast:
                return 1
            
        if capture_count is not None and attempts >= capture_count:
            break

        next_capture_deadline += config.capture_interval_seconds
        now = time.monotonic()
        if now > next_capture_deadline:
            skipped_slots = int(
                (now - next_capture_deadline) // config.capture_interval_seconds
            ) + 1
            next_capture_deadline += skipped_slots * config.capture_interval_seconds
            LOGGER.warning("capture_slots_skipped count=%s", skipped_slots)

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = parse_arguments(argv)

    if args.count is not None and args.count <= 0:
        LOGGER.error("--count must be greater than zero")
        return 2
    if args.camera_id is not None and args.camera_id < 0:
        LOGGER.error("--camera-id must be zero or greater")
        return 2

    try:
        config = AgentConfig.from_file(args.config)
    except ConfigurationError as exc:
        LOGGER.error("configuration_error detail=%s", exc)
        return 2

    if args.camera_id is not None:
        config = replace(config, camera_id=args.camera_id)

    capture_count = 1 if args.once else args.count
    lock_path = args.config.parent / "run" / "camera-agent.lock"

    try:
        with SingleInstanceLock(lock_path):
            LOGGER.info(
                "agent_started camera_slug=%s camera_id=%s interval_seconds=%s "
                "maximum_dimensions=%sx%s jpeg_quality=%s",
                config.camera_slug,
                config.camera_id,
                config.capture_interval_seconds,
                config.maximum_width,
                config.maximum_height,
                config.jpeg_quality,
            )
            return run_capture_schedule(
                config,
                capture_count=capture_count,
                fail_fast=args.fail_fast or args.once,
            )
    except AlreadyRunningError as exc:
        LOGGER.error("agent_already_running detail=%s", exc)
        return 3
    except KeyboardInterrupt:
        LOGGER.info("agent_stopped reason=keyboard_interrupt")
        return 130


if __name__ == "__main__":
    sys.exit(main())
