from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_CAMERA_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    agent_version: str
    api_base_url: str
    camera_slug: str
    camera_credential: str
    camera_id: int
    root_directory: Path
    capture_interval_seconds: int
    heartbeat_interval_seconds: int
    upload_poll_seconds: float
    cleanup_interval_seconds: int
    capture_timeout_seconds: int
    request_timeout_seconds: int
    maximum_width: int
    maximum_height: int
    jpeg_quality: int
    queue_max_bytes: int
    queue_max_age_hours: int
    allow_insecure_http: bool

    @property
    def queue_database_path(self) -> Path:
        return self.root_directory / "queue.db"

    @property
    def pending_directory(self) -> Path:
        return self.root_directory / "pending"

    @property
    def temporary_directory(self) -> Path:
        return self.root_directory / "tmp"

    @property
    def logs_directory(self) -> Path:
        return self.root_directory / "logs"

    @property
    def lock_file_path(self) -> Path:
        return self.root_directory / "camera-agent.lock"

    @property
    def image_upload_url(self) -> str:
        return f"{self.api_base_url}/api/v1/cameras/{self.camera_slug}/images"

    @property
    def heartbeat_url(self) -> str:
        return f"{self.api_base_url}/api/v1/cameras/{self.camera_slug}/heartbeats"

    def prepare_directories(self) -> None:
        for directory in (
            self.root_directory,
            self.pending_directory,
            self.temporary_directory,
            self.logs_directory,
        ):
            directory.mkdir(
                parents=True,
                exist_ok=True,
                mode=0o700,
            )


def load_config(path: Path) -> AgentConfig:
    resolved_path = path.expanduser().resolve()

    if not resolved_path.is_file():
        raise ConfigurationError(f"Configuration file does not exist: {resolved_path}")

    _validate_permissions(resolved_path)

    try:
        raw = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON configuration: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration root must be a JSON object")

    try:
        config = AgentConfig(
            agent_version=str(raw.get("agent_version", "0.4.0")),
            api_base_url=str(raw["api_base_url"]).rstrip("/"),
            camera_slug=str(raw["camera_slug"]),
            camera_credential=str(raw["camera_credential"]),
            camera_id=int(raw.get("camera_id", 0)),
            root_directory=Path(
                raw.get(
                    "root_directory",
                    "~/timelapse",
                )
            )
            .expanduser()
            .resolve(),
            capture_interval_seconds=int(
                raw.get(
                    "capture_interval_seconds",
                    60,
                )
            ),
            heartbeat_interval_seconds=int(
                raw.get(
                    "heartbeat_interval_seconds",
                    300,
                )
            ),
            upload_poll_seconds=float(raw.get("upload_poll_seconds", 2)),
            cleanup_interval_seconds=int(
                raw.get(
                    "cleanup_interval_seconds",
                    300,
                )
            ),
            capture_timeout_seconds=int(
                raw.get(
                    "capture_timeout_seconds",
                    30,
                )
            ),
            request_timeout_seconds=int(
                raw.get(
                    "request_timeout_seconds",
                    30,
                )
            ),
            maximum_width=int(raw.get("maximum_width", 1280)),
            maximum_height=int(raw.get("maximum_height", 720)),
            jpeg_quality=int(raw.get("jpeg_quality", 72)),
            queue_max_bytes=int(
                raw.get(
                    "queue_max_bytes",
                    2 * 1024 * 1024 * 1024,
                )
            ),
            queue_max_age_hours=int(raw.get("queue_max_age_hours", 48)),
            allow_insecure_http=bool(
                raw.get(
                    "allow_insecure_http",
                    False,
                )
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid configuration value: {exc}") from exc

    _validate_config(config)

    return config


def _validate_permissions(path: Path) -> None:
    if os.name != "posix":
        return

    mode = stat.S_IMODE(path.stat().st_mode)

    if mode & 0o077:
        raise ConfigurationError(
            f"{path} must not be accessible by group or others; run: chmod 600 {path}"
        )


def _validate_config(config: AgentConfig) -> None:
    parsed_url = urlparse(config.api_base_url)

    if not parsed_url.hostname:
        raise ConfigurationError("api_base_url must contain a hostname")

    if parsed_url.scheme != "https":
        local_host = parsed_url.hostname in {
            "127.0.0.1",
            "localhost",
        }

        if not (
            config.allow_insecure_http and local_host and parsed_url.scheme == "http"
        ):
            raise ConfigurationError(
                "api_base_url must use HTTPS; "
                "insecure HTTP is allowed only for localhost"
            )

    if not _CAMERA_SLUG_PATTERN.fullmatch(config.camera_slug):
        raise ConfigurationError(
            "camera_slug must contain lowercase letters, digits, and single hyphens"
        )

    if not config.camera_credential.startswith("cam_"):
        raise ConfigurationError("camera_credential has an invalid format")

    if config.camera_id < 0:
        raise ConfigurationError("camera_id must be zero or greater")

    if config.capture_interval_seconds < 30:
        raise ConfigurationError("capture_interval_seconds must be at least 30")

    if config.heartbeat_interval_seconds < 60:
        raise ConfigurationError("heartbeat_interval_seconds must be at least 60")

    if config.upload_poll_seconds <= 0:
        raise ConfigurationError("upload_poll_seconds must be greater than zero")

    if config.cleanup_interval_seconds < 30:
        raise ConfigurationError("cleanup_interval_seconds must be at least 30")

    if config.capture_timeout_seconds <= 0:
        raise ConfigurationError("capture_timeout_seconds must be positive")

    if config.request_timeout_seconds <= 0:
        raise ConfigurationError("request_timeout_seconds must be positive")

    if config.maximum_width <= 0 or config.maximum_height <= 0:
        raise ConfigurationError("maximum image dimensions must be positive")

    if not 1 <= config.jpeg_quality <= 100:
        raise ConfigurationError("jpeg_quality must be between 1 and 100")

    if config.queue_max_bytes <= 0:
        raise ConfigurationError("queue_max_bytes must be positive")

    if config.queue_max_age_hours <= 0:
        raise ConfigurationError("queue_max_age_hours must be positive")
