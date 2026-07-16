from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import AuditEvent, ExportPart, Image
from timelapse.models.enums import ImageStorageState
from timelapse.services.image_files import fsync_directory


@dataclass(frozen=True)
class ReconciliationResult:
    missing_files: int = 0
    orphaned_files: int = 0
    mismatched_files: int = 0
    stale_staging_rows: int = 0
    stale_temp_files: int = 0
    stale_export_files: int = 0


async def process_reconciliation_once(
    *,
    session: AsyncSession,
    storage_root: Path,
    now: datetime,
    stale_staging_age: timedelta = timedelta(hours=1),
    stale_temp_age: timedelta = timedelta(hours=1),
    stale_export_age: timedelta = timedelta(hours=6),
) -> ReconciliationResult:
    missing_files = await _mark_missing_database_files(
        session=session,
        now=now,
    )
    mismatched_files = await _audit_mismatched_database_files(
        session=session,
        now=now,
    )
    stale_staging_rows = await _mark_stale_staging_rows(
        session=session,
        now=now,
        stale_staging_age=stale_staging_age,
    )
    orphaned_files = await _quarantine_orphaned_image_files(
        session=session,
        storage_root=storage_root,
        now=now,
    )
    stale_temp_files = await _delete_stale_files(
        root=storage_root / "tmp",
        referenced_paths=set(),
        now=now,
        maximum_age=stale_temp_age,
    )
    referenced_export_paths = set(await session.scalars(select(ExportPart.storage_path)))
    stale_export_files = await _delete_stale_files(
        root=storage_root / "exports",
        referenced_paths=referenced_export_paths,
        now=now,
        maximum_age=stale_export_age,
    )

    return ReconciliationResult(
        missing_files=missing_files,
        orphaned_files=orphaned_files,
        mismatched_files=mismatched_files,
        stale_staging_rows=stale_staging_rows,
        stale_temp_files=stale_temp_files,
        stale_export_files=stale_export_files,
    )


async def _mark_missing_database_files(
    *,
    session: AsyncSession,
    now: datetime,
) -> int:
    images = (
        await session.scalars(
            select(Image)
            .where(Image.storage_state == ImageStorageState.STORED)
            .where(Image.deleted_at.is_(None))
            .order_by(Image.id)
        )
    ).all()
    count = 0

    for image in images:
        if await _path_exists(Path(image.storage_path)):
            continue

        image.storage_state = ImageStorageState.MISSING
        session.add(
            AuditEvent(
                occurred_at=now,
                event_type="reconciliation.image_missing",
                camera_id=image.camera_id,
                outcome="detected",
                details={"image_id": str(image.id)},
            )
        )
        count += 1

    return count


async def _audit_mismatched_database_files(
    *,
    session: AsyncSession,
    now: datetime,
) -> int:
    images = (
        await session.scalars(
            select(Image)
            .where(Image.storage_state == ImageStorageState.STORED)
            .where(Image.deleted_at.is_(None))
            .order_by(Image.id)
        )
    ).all()
    count = 0

    for image in images:
        path = Path(image.storage_path)
        if not await _path_exists(path):
            continue

        actual_size = (await asyncio.to_thread(path.stat)).st_size
        actual_sha256 = await asyncio.to_thread(_sha256_file, path)

        if actual_size == image.file_size_bytes and actual_sha256 == image.sha256:
            continue

        session.add(
            AuditEvent(
                occurred_at=now,
                event_type="reconciliation.image_mismatch",
                camera_id=image.camera_id,
                outcome="detected",
                details={
                    "image_id": str(image.id),
                    "expected_size": image.file_size_bytes,
                    "actual_size": actual_size,
                },
            )
        )
        count += 1

    return count


async def _mark_stale_staging_rows(
    *,
    session: AsyncSession,
    now: datetime,
    stale_staging_age: timedelta,
) -> int:
    cutoff = now - stale_staging_age
    images = (
        await session.scalars(
            select(Image)
            .where(Image.storage_state == ImageStorageState.STAGING)
            .where(Image.received_at_utc < cutoff)
            .order_by(Image.received_at_utc, Image.id)
        )
    ).all()

    for image in images:
        image.storage_state = ImageStorageState.MISSING
        session.add(
            AuditEvent(
                occurred_at=now,
                event_type="reconciliation.stale_staging_row",
                camera_id=image.camera_id,
                outcome="detected",
                details={"image_id": str(image.id)},
            )
        )

    return len(images)


async def _quarantine_orphaned_image_files(
    *,
    session: AsyncSession,
    storage_root: Path,
    now: datetime,
) -> int:
    del now
    images_root = storage_root / "images"
    known_paths = set(
        await session.scalars(
            select(Image.storage_path)
            .where(
                Image.storage_state.in_({ImageStorageState.STORED, ImageStorageState.DELETING})
            )
            .where(Image.deleted_at.is_(None))
        )
    )
    quarantine_root = storage_root / "quarantine" / "orphans"
    count = 0

    for path in await _list_files(images_root):
        if str(path) in known_paths:
            continue

        destination = quarantine_root / f"{uuid4()}_{path.name}"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        await asyncio.to_thread(os.replace, path, destination)
        await asyncio.to_thread(fsync_directory, destination.parent)
        count += 1

    return count


async def _delete_stale_files(
    *,
    root: Path,
    referenced_paths: set[str],
    now: datetime,
    maximum_age: timedelta,
) -> int:
    count = 0

    for path in await _list_files(root):
        if str(path) in referenced_paths:
            continue

        modified_at = datetime.fromtimestamp(
            (await asyncio.to_thread(path.stat)).st_mtime, tz=UTC
        )
        if modified_at > now - maximum_age:
            continue

        await asyncio.to_thread(path.unlink)
        count += 1

    return count


async def _path_exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)


async def _list_files(root: Path) -> list[Path]:
    if not await _path_exists(root):
        return []

    return await asyncio.to_thread(
        lambda: sorted(file for file in root.rglob("*") if file.is_file())
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
