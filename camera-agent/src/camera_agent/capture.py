from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from camera_agent.compressor import JpegMetadata, normalize_jpeg
from camera_agent.configuration import AgentConfig


class CaptureError(RuntimeError):
    """Raised when Termux cannot produce a usable camera file."""


@dataclass(frozen=True)
class CaptureResult:
    capture_id: UUID
    captured_at_utc: datetime
    file_path: Path
    width_pixels: int
    height_pixels: int
    file_size_bytes: int
    sha256: str


class TermuxCamera:
    def __init__(self, executable: str = "termux-camera-photo") -> None:
        self._executable = executable

    def capture(
        self, *, camera_id: int, destination: Path, timeout_seconds: int
    ) -> None:
        executable_path = shutil.which(self._executable)
        if executable_path is None:
            raise CaptureError(
                "termux-camera-photo was not found; install the termux-api package"
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)

        try:
            completed = subprocess.run(
                [
                    executable_path,
                    "-c",
                    str(camera_id),
                    str(destination),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            destination.unlink(missing_ok=True)
            raise CaptureError(
                f"camera command timed out after {timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise CaptureError(f"camera command could not be started: {exc}") from exc

        if completed.returncode != 0:
            destination.unlink(missing_ok=True)
            detail = (completed.stderr or completed.stdout or "no error detail").strip()
            raise CaptureError(
                f"camera command exited with {completed.returncode}: {detail}"
            )

        if not destination.is_file():
            raise CaptureError("camera command succeeded but did not create a file")
        if destination.stat().st_size <= 0:
            destination.unlink(missing_ok=True)
            raise CaptureError("camera command created an empty file")


def capture_frame(
    config: AgentConfig,
    *,
    camera: TermuxCamera | None = None,
) -> CaptureResult:
    camera = camera or TermuxCamera()
    capture_id = uuid4()
    captured_at_utc = datetime.now(timezone.utc)

    day_directory = (
        config.output_directory
        / config.camera_slug
        / captured_at_utc.strftime("%Y-%m-%d")
    )
    timestamp = captured_at_utc.strftime("%Y%m%dT%H%M%SZ")
    destination_path = day_directory / f"{timestamp}_{capture_id}.jpg"
    raw_path = day_directory / f".raw_{capture_id}.jpg"

    try:
        camera.capture(
            camera_id=config.camera_id,
            destination=raw_path,
            timeout_seconds=config.capture_timeout_seconds,
        )
        metadata: JpegMetadata = normalize_jpeg(
            raw_path,
            destination_path,
            maximum_width=config.maximum_width,
            maximum_height=config.maximum_height,
            quality=config.jpeg_quality,
        )
    finally:
        raw_path.unlink(missing_ok=True)

    return CaptureResult(
        capture_id=capture_id,
        captured_at_utc=captured_at_utc,
        file_path=destination_path,
        width_pixels=metadata.width_pixels,
        height_pixels=metadata.height_pixels,
        file_size_bytes=metadata.file_size_bytes,
        sha256=metadata.sha256,
    )
