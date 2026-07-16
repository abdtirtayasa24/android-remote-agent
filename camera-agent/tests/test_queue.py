from __future__ import annotations

from datetime import (
    UTC,
    datetime,
    timedelta,
)
from pathlib import Path
from uuid import uuid4

from camera_agent.queue import (
    QueueItem,
    QueueStore,
    utc_datetime_text,
)


def create_item(
    tmp_path: Path,
    *,
    captured_at: datetime,
    size: int = 10,
) -> QueueItem:
    capture_id = str(uuid4())
    path = tmp_path / f"{capture_id}.jpg"
    path.write_bytes(b"x" * size)

    return QueueItem(
        capture_id=capture_id,
        file_path=path,
        captured_at_utc=(utc_datetime_text(captured_at)),
        capture_source="scheduled",
        file_size_bytes=size,
        sha256="a" * 64,
    )


def test_interrupted_upload_is_recovered(
    tmp_path: Path,
) -> None:
    queue = QueueStore(tmp_path / "queue.db")
    queue.initialize()

    item = create_item(
        tmp_path,
        captured_at=datetime.now(UTC),
    )
    queue.enqueue(item)

    claimed = queue.claim_due()

    assert claimed is not None
    assert claimed.state == "uploading"

    recovered = queue.recover_interrupted_uploads()

    assert recovered == 1

    claimed_again = queue.claim_due()

    assert claimed_again is not None
    assert claimed_again.capture_id == item.capture_id


def test_local_file_is_deleted_only_after_confirmation(
    tmp_path: Path,
) -> None:
    queue = QueueStore(tmp_path / "queue.db")
    queue.initialize()

    item = create_item(
        tmp_path,
        captured_at=datetime.now(UTC),
    )
    queue.enqueue(item)
    queue.claim_due()

    assert item.file_path.exists()

    confirmed = queue.confirm_uploaded(
        item.capture_id,
        item.file_path,
    )

    assert confirmed
    assert not item.file_path.exists()
    assert queue.get(item.capture_id) is None


def test_age_limit_drops_oldest_scheduled_image(
    tmp_path: Path,
) -> None:
    queue = QueueStore(tmp_path / "queue.db")
    queue.initialize()

    now = datetime.now(UTC)
    expired = create_item(
        tmp_path,
        captured_at=now - timedelta(hours=49),
    )
    recent = create_item(
        tmp_path,
        captured_at=now - timedelta(hours=1),
    )

    queue.enqueue(expired)
    queue.enqueue(recent)

    dropped = queue.enforce_limits(
        maximum_bytes=1024,
        maximum_age_hours=48,
        now=now,
    )

    assert dropped == 1
    assert queue.get(expired.capture_id) is None
    assert queue.get(recent.capture_id) is not None
    assert queue.runtime_state().dropped_image_count == 1


def test_size_limit_drops_oldest_scheduled_first(
    tmp_path: Path,
) -> None:
    queue = QueueStore(tmp_path / "queue.db")
    queue.initialize()

    now = datetime.now(UTC)
    oldest = create_item(
        tmp_path,
        captured_at=now - timedelta(minutes=3),
        size=10,
    )
    middle = create_item(
        tmp_path,
        captured_at=now - timedelta(minutes=2),
        size=10,
    )
    newest = create_item(
        tmp_path,
        captured_at=now - timedelta(minutes=1),
        size=10,
    )

    queue.enqueue(oldest)
    queue.enqueue(middle)
    queue.enqueue(newest)

    dropped = queue.enforce_limits(
        maximum_bytes=20,
        maximum_age_hours=48,
        now=now,
    )

    assert dropped == 1
    assert queue.get(oldest.capture_id) is None
    assert queue.get(middle.capture_id) is not None
    assert queue.get(newest.capture_id) is not None
