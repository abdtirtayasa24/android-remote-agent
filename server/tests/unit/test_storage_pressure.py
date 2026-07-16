from __future__ import annotations

from pydantic import SecretStr
from timelapse.configuration import Settings
from timelapse.services.storage_pressure import (
    DiskUsage,
    StoragePressureState,
    classify_disk_pressure,
)


def test_storage_pressure_defaults_are_enabled_for_production() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://user:pass@example.com/timelapse"),
        database_migration_url=SecretStr("postgresql://user:pass@example.com/timelapse"),
        camera_token_pepper=SecretStr("x" * 32),
    )

    assert settings.storage_hard_min_free_bytes > 0
    assert settings.storage_severe_min_free_bytes >= settings.storage_hard_min_free_bytes


def test_disk_pressure_is_normal_above_thresholds() -> None:
    state = classify_disk_pressure(
        DiskUsage(total=1_000, used=100, free=900),
        hard_min_free_bytes=100,
        severe_min_free_bytes=200,
    )

    assert state == StoragePressureState.NORMAL


def test_disk_pressure_is_severe_below_severe_threshold() -> None:
    state = classify_disk_pressure(
        DiskUsage(total=1_000, used=850, free=150),
        hard_min_free_bytes=100,
        severe_min_free_bytes=200,
    )

    assert state == StoragePressureState.SEVERE


def test_disk_pressure_is_hard_limit_below_hard_threshold() -> None:
    state = classify_disk_pressure(
        DiskUsage(total=1_000, used=950, free=50),
        hard_min_free_bytes=100,
        severe_min_free_bytes=200,
    )

    assert state == StoragePressureState.HARD_LIMIT
