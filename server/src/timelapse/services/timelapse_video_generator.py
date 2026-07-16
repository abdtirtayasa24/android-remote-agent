from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

FfmpegRunner = Callable[[list[str], int], int]


class TimelapseVideoGenerationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GeneratedTimelapseVideo:
    path: Path
    file_size_bytes: int
    sha256: str


def build_timelapse_video(
    *,
    output_directory: Path,
    job_id: str,
    image_paths: tuple[Path, ...],
    frame_rate: int,
    runner: FfmpegRunner | None = None,
    timeout_seconds: int = 1800,
) -> GeneratedTimelapseVideo:
    if not image_paths:
        raise TimelapseVideoGenerationError("no_images")

    final_path = output_directory / f"{job_id}.mp4"

    try:
        output_directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        final_path.unlink(missing_ok=True)
        execute = runner or _run_ffmpeg

        with TemporaryDirectory(
            dir=output_directory,
            prefix=f".{job_id}-",
        ) as temporary:
            temporary_directory = Path(temporary)

            for ordinal, image_path in enumerate(image_paths, start=1):
                if not image_path.is_file():
                    raise TimelapseVideoGenerationError("image_file_missing")

                staged_path = temporary_directory / f"frame_{ordinal:06d}.jpg"
                staged_path.symlink_to(image_path.resolve())

            temporary_output = temporary_directory / "output.mp4"
            command = _ffmpeg_command(
                frame_pattern=temporary_directory / "frame_%06d.jpg",
                output_path=temporary_output,
                frame_rate=frame_rate,
            )

            try:
                return_code = execute(command, timeout_seconds)
            except subprocess.TimeoutExpired:
                raise TimelapseVideoGenerationError("ffmpeg_timeout") from None
            except OSError:
                raise TimelapseVideoGenerationError("ffmpeg_unavailable") from None

            if return_code != 0 or not temporary_output.is_file():
                raise TimelapseVideoGenerationError("ffmpeg_failed")

            os.replace(temporary_output, final_path)

        return GeneratedTimelapseVideo(
            path=final_path,
            file_size_bytes=final_path.stat().st_size,
            sha256=_sha256_file(final_path),
        )
    except TimelapseVideoGenerationError:
        raise
    except OSError:
        try:
            final_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise TimelapseVideoGenerationError("video_io_error") from None


def _ffmpeg_command(
    *,
    frame_pattern: Path,
    output_path: Path,
    frame_rate: int,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-framerate",
        str(frame_rate),
        "-i",
        str(frame_pattern),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _run_ffmpeg(command: list[str], timeout_seconds: int) -> int:
    result = subprocess.run(  # noqa: S603 - fixed executable and argument list
        command,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout_seconds,
    )
    return result.returncode


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()
