from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from timelapse.services import voice_note_commands


def test_voice_audio_normalization_uses_ffmpeg_argument_list(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "voice.oga"
    destination = tmp_path / "voice.mp3"
    source.write_bytes(b"telegram-voice")
    observed_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        observed_command.extend(command)
        destination.write_bytes(b"normalized-mp3")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(voice_note_commands.subprocess, "run", fake_run)

    normalized = voice_note_commands.normalize_voice_audio(source, destination)

    assert observed_command == [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "96k",
        str(destination),
    ]
    assert normalized.file_size_bytes == len(b"normalized-mp3")
    assert normalized.sha256 == hashlib.sha256(b"normalized-mp3").hexdigest()
