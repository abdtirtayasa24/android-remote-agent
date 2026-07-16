from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from timelapse.configuration import Settings
from timelapse.services.image_files import UploadRejectedError


class StoragePressureState(StrEnum):
    NORMAL = "normal"
    SEVERE = "severe"
    HARD_LIMIT = "hard_limit"


@dataclass(frozen=True)
class DiskUsage:
    total: int
    used: int
    free: int


def get_disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(
        total=usage.total,
        used=usage.used,
        free=usage.free,
    )


def classify_disk_pressure(
    usage: DiskUsage,
    *,
    hard_min_free_bytes: int,
    severe_min_free_bytes: int,
) -> StoragePressureState:
    if usage.free < hard_min_free_bytes:
        return StoragePressureState.HARD_LIMIT
    if usage.free < severe_min_free_bytes:
        return StoragePressureState.SEVERE
    return StoragePressureState.NORMAL


def get_storage_pressure_state(
    *,
    settings: Settings,
    path: Path | None = None,
) -> StoragePressureState:
    check_path = path or settings.storage_root
    check_path.mkdir(parents=True, exist_ok=True, mode=0o750)
    return classify_disk_pressure(
        get_disk_usage(check_path),
        hard_min_free_bytes=settings.storage_hard_min_free_bytes,
        severe_min_free_bytes=settings.storage_severe_min_free_bytes,
    )


def reject_upload_if_hard_limit(settings: Settings) -> None:
    if get_storage_pressure_state(settings=settings) == StoragePressureState.HARD_LIMIT:
        raise UploadRejectedError(
            status_code=507,
            code="storage_pressure_hard_limit",
        )
