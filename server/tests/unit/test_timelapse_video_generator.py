from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from timelapse.services.timelapse_video_generator import (
    TimelapseVideoGenerationError,
    build_timelapse_video,
)


def test_video_generator_stages_ordered_frames_and_records_output(
    tmp_path: Path,
) -> None:
    first = tmp_path / "images" / "first.jpg"
    second = tmp_path / "images" / "second.jpg"
    first.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    observed_frames: list[bytes] = []
    observed_command: list[str] = []

    def successful_runner(command: list[str], timeout_seconds: int) -> int:
        del timeout_seconds
        observed_command.extend(command)
        frame_directory = Path(command[command.index("-i") + 1]).parent
        observed_frames.extend(
            [
                (frame_directory / "frame_000001.jpg").read_bytes(),
                (frame_directory / "frame_000002.jpg").read_bytes(),
            ]
        )
        Path(command[-1]).write_bytes(b"generated-mp4")
        return 0

    result = build_timelapse_video(
        output_directory=tmp_path / "videos",
        job_id="job-123",
        image_paths=(first, second),
        frame_rate=24,
        runner=successful_runner,
    )

    assert observed_frames == [b"first", b"second"]
    assert observed_command[:7] == [
        "ffmpeg",
        "-y",
        "-framerate",
        "24",
        "-i",
        str(Path(observed_command[5])),
        "-vf",
    ]
    assert "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p" in observed_command
    assert result.path == tmp_path / "videos" / "job-123.mp4"
    assert result.path.read_bytes() == b"generated-mp4"
    assert result.file_size_bytes == len(b"generated-mp4")
    assert len(result.sha256) == 64


def test_video_generator_normalizes_filesystem_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"frame")
    output_directory = tmp_path / "videos"

    def fail_symlink(self: Path, target: Path) -> None:
        raise OSError("filesystem unavailable")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink)

    with pytest.raises(TimelapseVideoGenerationError, match="video_io_error"):
        build_timelapse_video(
            output_directory=output_directory,
            job_id="job-123",
            image_paths=(image,),
            frame_rate=24,
        )

    assert not (output_directory / "job-123.mp4").exists()


def test_video_generator_removes_partial_output_after_ffmpeg_failure(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"frame")
    output_directory = tmp_path / "videos"
    output_directory.mkdir()
    (output_directory / "job-123.mp4").write_bytes(b"orphaned-previous-attempt")

    def failing_runner(command: list[str], timeout_seconds: int) -> int:
        del timeout_seconds
        Path(command[-1]).write_bytes(b"partial")
        return subprocess.CalledProcessError(returncode=1, cmd=command).returncode

    with pytest.raises(TimelapseVideoGenerationError, match="ffmpeg_failed"):
        build_timelapse_video(
            output_directory=output_directory,
            job_id="job-123",
            image_paths=(image,),
            frame_rate=24,
            runner=failing_runner,
        )

    assert not (output_directory / "job-123.mp4").exists()
