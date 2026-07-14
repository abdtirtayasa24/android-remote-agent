from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CAMERA_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ALLOWED_KEYS = {
    "camera_slug",
    "camera_id",
    "capture_interval_seconds",
    "maximum_width",
    "maximum_height",
    "jpeg_quality",
    "capture_timeout_seconds",
    "output_directory",
}


class ConfigurationError(ValueError):
    """Raised when the agent configuration is missing or invalid."""


@dataclass(frozen=True)
class AgentConfig:
    camera_slug: str
    camera_id: int
    capture_interval_seconds: int = 60
    maximum_width: int = 1280
    maximum_height: int = 720
    jpeg_quality: int = 72
    capture_timeout_seconds: int = 30
    output_directory: Path = Path.home() / "timelapse" / "validation-captures"

    @classmethod
    def from_file(cls, path: Path) -> AgentConfig:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"configuration file does not exist: {path}")
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"configuration file is not valid JSON: line {exc.lineno}, column {exc.colno}"
            ) from exc
        
        if not isinstance(raw, dict):
            raise ConfigurationError("configuration root must be a JSON object")
        
        unknown_keys = sorted(set(raw) - _ALLOWED_KEYS)
        if unknown_keys:
            raise ConfigurationError(
                f"unknown configuration keys: {', '.join(unknown_keys)}"
            )
        
        camera_slug = _require_string(raw, "camera_slug")
        if not _CAMERA_SLUG_PATTERN.fullmatch(camera_slug):
            raise ConfigurationError(
                "camera_slug must contain lowercase letters, numbers, and single hyphens"
            )
        
        camera_id = _require_integer(raw, "camera_id")
        capture_interval_seconds = _optional_integer(raw, "capture_interval_seconds", 60)
        maximum_width = _optional_integer(raw, "maximum_width", 1280)
        maximum_height = _optional_integer(raw, "maximum_height", 720)
        jpeg_quality = _optional_integer(raw, "jpeg_quality", 72)
        capture_timeout_seconds = _optional_integer(raw, "capture_timeout_seconds", 30)

        output_directory_value = raw.get(
            "output_directory",
            "$HOME/timelapse/validation-captures",
        )
        if not isinstance(output_directory_value, str) or not output_directory_value.strip():
            raise ConfigurationError("output directory must be a non-empty string")
        
        output_directory = Path(
            os.path.expandvars(os.path.expanduser(output_directory_value))
        )

        if camera_id < 0:
            raise ConfigurationError("camera_id must be zero or greater")
        if capture_interval_seconds < 30:
            raise ConfigurationError("capture_interval_seconds must be at least 30")
        if maximum_width <= 0 or maximum_height <= 0:
            raise ConfigurationError("maximum image dimensions must be positive")
        if not 1 <= jpeg_quality <= 100:
            raise ConfigurationError("jpeg_quality must be between 1 and 100")
        if not 5 <= capture_timeout_seconds <= 120:
            raise ConfigurationError("capture_timeout_seconds must be between 5 and 120")
        
        return cls(
            camera_slug=camera_slug,
            camera_id=camera_id,
            capture_interval_seconds=capture_interval_seconds,
            maximum_width=maximum_width,
            maximum_height=maximum_height,
            jpeg_quality=jpeg_quality,
            capture_timeout_seconds=capture_timeout_seconds,
            output_directory=output_directory,
        )
    

def _require_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value.strip()


def _require_integer(raw: dict[str, Any], key: str) -> int:
    if key not in raw:
        raise ConfigurationError(f"missing required configuration key: {key}")
    return _as_integer(raw[key], key)


def _optional_integer(raw: dict[str, Any], key: str, default: int) -> int:
    return _as_integer(raw.get(key, default), key)


def _as_integer(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{key} must be an integer")
    return value
