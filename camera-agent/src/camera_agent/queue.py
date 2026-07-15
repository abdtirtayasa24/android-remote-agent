from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

UTC = timezone.utc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_queue (
    capture_id          TEXT PRIMARY KEY,
    file_path           TEXT NOT NULL UNIQUE,
    captured_at_utc     TEXT NOT NULL,
    capture_source      TEXT NOT NULL DEFAULT 'scheduled'
                        CHECK (
                            capture_source IN (
                                'scheduled',
                                'manual',
                                'motion'
                            )
                        ),
    file_size_bytes     INTEGER NOT NULL
                        CHECK (file_size_bytes > 0),
    sha256              TEXT NOT NULL
                        CHECK (length(sha256) = 64),
    state               TEXT NOT NULL DEFAULT 'pending'
                        CHECK (
                            state IN (
                                'pending',
                                'uploading',
                                'uploaded',
                                'failed'
                            )
                        ),
    retry_count         INTEGER NOT NULL DEFAULT 0
                        CHECK (retry_count >= 0),
    next_attempt_at_utc TEXT NOT NULL,
    last_error          TEXT,
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_upload_queue_due
    ON upload_queue (
        state,
        next_attempt_at_utc
    );

CREATE INDEX IF NOT EXISTS idx_upload_queue_cleanup
    ON upload_queue (
        capture_source,
        captured_at_utc
    );

CREATE TABLE IF NOT EXISTS agent_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO agent_state
    (key, value)
VALUES
    ('dropped_image_count', '0'),
    ('consecutive_capture_failures', '0'),
    ('consecutive_heartbeat_failures', '0'),
    ('last_capture_at_utc', ''),
    ('last_upload_at_utc', ''),
    ('last_error_code', '');
"""


@dataclass(frozen=True)
class QueueItem:
    capture_id: str
    file_path: Path
    captured_at_utc: str
    capture_source: str
    file_size_bytes: int
    sha256: str
    state: str = "pending"
    retry_count: int = 0
    next_attempt_at_utc: str = ""
    last_error: str | None = None


@dataclass(frozen=True)
class QueueMetrics:
    pending_image_count: int
    pending_image_bytes: int
    oldest_pending_at_utc: str | None


@dataclass(frozen=True)
class RuntimeState:
    dropped_image_count: int
    consecutive_capture_failures: int
    consecutive_heartbeat_failures: int
    last_capture_at_utc: str | None
    last_upload_at_utc: str | None
    last_error_code: str | None


class QueueStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        with self._open() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(_SCHEMA)

    def enqueue(self, item: QueueItem) -> None:
        now = utc_now_text()

        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO upload_queue (
                    capture_id,
                    file_path,
                    captured_at_utc,
                    capture_source,
                    file_size_bytes,
                    sha256,
                    state,
                    retry_count,
                    next_attempt_at_utc,
                    last_error,
                    created_at_utc,
                    updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, ?, ?)
                """,
                (
                    item.capture_id,
                    str(item.file_path),
                    item.captured_at_utc,
                    item.capture_source,
                    item.file_size_bytes,
                    item.sha256,
                    item.next_attempt_at_utc or now,
                    now,
                    now,
                ),
            )

    def claim_due(
        self,
        *,
        now: datetime | None = None,
    ) -> QueueItem | None:
        now_text = utc_datetime_text(now or datetime.now(UTC))

        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM upload_queue
                WHERE state = 'pending'
                  AND next_attempt_at_utc <= ?
                ORDER BY captured_at_utc, created_at_utc
                LIMIT 1
                """,
                (now_text,),
            ).fetchone()

            if row is None:
                return None

            updated = connection.execute(
                """
                UPDATE upload_queue
                SET state = 'uploading',
                    updated_at_utc = ?
                WHERE capture_id = ?
                  AND state = 'pending'
                """,
                (
                    now_text,
                    row["capture_id"],
                ),
            )

            if updated.rowcount != 1:
                return None

            return replace(
                self._row_to_item(row),
                state="uploading",
            )

    def reschedule(
        self,
        capture_id: str,
        *,
        delay_seconds: int,
        error_code: str,
    ) -> None:
        now = datetime.now(UTC)
        next_attempt = now + timedelta(seconds=delay_seconds)

        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE upload_queue
                SET state = 'pending',
                    retry_count = retry_count + 1,
                    next_attempt_at_utc = ?,
                    last_error = ?,
                    updated_at_utc = ?
                WHERE capture_id = ?
                  AND state = 'uploading'
                """,
                (
                    utc_datetime_text(next_attempt),
                    error_code[:128],
                    utc_datetime_text(now),
                    capture_id,
                ),
            )

            self._set_state_in_connection(
                connection,
                "last_error_code",
                error_code[:128],
            )

    def mark_failed(
        self,
        capture_id: str,
        *,
        error_code: str,
    ) -> None:
        now = utc_now_text()

        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE upload_queue
                SET state = 'failed',
                    retry_count = retry_count + 1,
                    last_error = ?,
                    updated_at_utc = ?
                WHERE capture_id = ?
                  AND state = 'uploading'
                """,
                (
                    error_code[:128],
                    now,
                    capture_id,
                ),
            )

            self._set_state_in_connection(
                connection,
                "last_error_code",
                error_code[:128],
            )

    def confirm_uploaded(
        self,
        capture_id: str,
        file_path: Path,
    ) -> bool:
        now = utc_now_text()

        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE upload_queue
                SET state = 'uploaded',
                    last_error = NULL,
                    updated_at_utc = ?
                WHERE capture_id = ?
                  AND state = 'uploading'
                """,
                (
                    now,
                    capture_id,
                ),
            )

            if updated.rowcount != 1:
                return False

        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            # The row remains "uploaded". Startup and cleanup
            # recovery will retry local deletion without
            # retransmitting the image.
            return False

        with self._transaction() as connection:
            connection.execute(
                """
                DELETE FROM upload_queue
                WHERE capture_id = ?
                  AND state = 'uploaded'
                """,
                (capture_id,),
            )

        return True

    def finalize_uploaded_files(self) -> int:
        with self._open() as connection:
            rows = connection.execute(
                """
                SELECT capture_id, file_path
                FROM upload_queue
                WHERE state = 'uploaded'
                ORDER BY updated_at_utc
                """
            ).fetchall()

        finalized = 0

        for row in rows:
            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except OSError:
                continue

            with self._transaction() as connection:
                deleted = connection.execute(
                    """
                    DELETE FROM upload_queue
                    WHERE capture_id = ?
                      AND state = 'uploaded'
                    """,
                    (row["capture_id"],),
                )

            finalized += deleted.rowcount

        return finalized

    def recover_interrupted_uploads(self) -> int:
        now = utc_now_text()

        with self._transaction() as connection:
            result = connection.execute(
                """
                UPDATE upload_queue
                SET state = 'pending',
                    next_attempt_at_utc = ?,
                    last_error = 'agent_restarted',
                    updated_at_utc = ?
                WHERE state = 'uploading'
                """,
                (
                    now,
                    now,
                ),
            )

        return result.rowcount

    def enforce_limits(
        self,
        *,
        maximum_bytes: int,
        maximum_age_hours: int,
        now: datetime | None = None,
    ) -> int:
        self.finalize_uploaded_files()

        current_time = now or datetime.now(UTC)
        age_cutoff = current_time - timedelta(hours=maximum_age_hours)
        dropped = 0

        with self._open() as connection:
            expired_rows = connection.execute(
                """
                SELECT capture_id
                FROM upload_queue
                WHERE capture_source = 'scheduled'
                  AND state IN ('pending', 'failed')
                  AND captured_at_utc < ?
                ORDER BY captured_at_utc
                """,
                (utc_datetime_text(age_cutoff),),
            ).fetchall()

        for row in expired_rows:
            if self._drop_scheduled(row["capture_id"]):
                dropped += 1

        while self.metrics().pending_image_bytes > maximum_bytes:
            with self._open() as connection:
                oldest = connection.execute(
                    """
                    SELECT capture_id
                    FROM upload_queue
                    WHERE capture_source = 'scheduled'
                      AND state IN ('pending', 'failed')
                    ORDER BY captured_at_utc, created_at_utc
                    LIMIT 1
                    """
                ).fetchone()

            if oldest is None:
                break

            if self._drop_scheduled(oldest["capture_id"]):
                dropped += 1
            else:
                break

        return dropped

    def metrics(self) -> QueueMetrics:
        with self._open() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS image_count,
                    COALESCE(
                        SUM(file_size_bytes),
                        0
                    ) AS image_bytes,
                    MIN(captured_at_utc) AS oldest
                FROM upload_queue
                WHERE state IN (
                    'pending',
                    'uploading',
                    'failed'
                )
                """
            ).fetchone()

        return QueueMetrics(
            pending_image_count=int(row["image_count"]),
            pending_image_bytes=int(row["image_bytes"]),
            oldest_pending_at_utc=row["oldest"],
        )

    def runtime_state(self) -> RuntimeState:
        with self._open() as connection:
            rows = connection.execute(
                """
                SELECT key, value
                FROM agent_state
                """
            ).fetchall()

        values = {row["key"]: row["value"] for row in rows}

        return RuntimeState(
            dropped_image_count=int(
                values.get(
                    "dropped_image_count",
                    "0",
                )
            ),
            consecutive_capture_failures=int(
                values.get(
                    "consecutive_capture_failures",
                    "0",
                )
            ),
            consecutive_heartbeat_failures=int(
                values.get(
                    "consecutive_heartbeat_failures",
                    "0",
                )
            ),
            last_capture_at_utc=(values.get("last_capture_at_utc") or None),
            last_upload_at_utc=(values.get("last_upload_at_utc") or None),
            last_error_code=(values.get("last_error_code") or None),
        )

    def record_capture_success(
        self,
        captured_at_utc: str,
    ) -> None:
        with self._transaction() as connection:
            self._set_state_in_connection(
                connection,
                "consecutive_capture_failures",
                "0",
            )
            self._set_state_in_connection(
                connection,
                "last_capture_at_utc",
                captured_at_utc,
            )

    def record_capture_failure(
        self,
        error_code: str,
    ) -> None:
        with self._transaction() as connection:
            self._increment_state_in_connection(
                connection,
                "consecutive_capture_failures",
                1,
            )
            self._set_state_in_connection(
                connection,
                "last_error_code",
                error_code[:128],
            )

    def record_upload_success(
        self,
        received_at_utc: str,
    ) -> None:
        with self._transaction() as connection:
            self._set_state_in_connection(
                connection,
                "last_upload_at_utc",
                received_at_utc,
            )

    def record_heartbeat_failure(
        self,
        error_code: str,
    ) -> None:
        with self._transaction() as connection:
            self._increment_state_in_connection(
                connection,
                "consecutive_heartbeat_failures",
                1,
            )
            self._set_state_in_connection(
                connection,
                "last_error_code",
                error_code[:128],
            )

    def record_heartbeat_success(self) -> None:
        with self._transaction() as connection:
            self._set_state_in_connection(
                connection,
                "consecutive_heartbeat_failures",
                "0",
            )

    def acknowledge_dropped_images(
        self,
        reported_count: int,
    ) -> None:
        with self._transaction() as connection:
            current = self._get_state_int_in_connection(
                connection,
                "dropped_image_count",
            )
            remaining = max(
                current - reported_count,
                0,
            )
            self._set_state_in_connection(
                connection,
                "dropped_image_count",
                str(remaining),
            )

    def get(
        self,
        capture_id: str,
    ) -> QueueItem | None:
        with self._open() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM upload_queue
                WHERE capture_id = ?
                """,
                (capture_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_item(row)

    def _drop_scheduled(
        self,
        capture_id: str,
    ) -> bool:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT file_path, state, capture_source
                FROM upload_queue
                WHERE capture_id = ?
                """,
                (capture_id,),
            ).fetchone()

            if row is None:
                return False

            if row["capture_source"] != "scheduled" or row["state"] not in {
                "pending",
                "failed",
            }:
                return False

            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except OSError:
                return False

            deleted = connection.execute(
                """
                DELETE FROM upload_queue
                WHERE capture_id = ?
                  AND capture_source = 'scheduled'
                  AND state IN ('pending', 'failed')
                """,
                (capture_id,),
            )

            if deleted.rowcount != 1:
                return False

            self._increment_state_in_connection(
                connection,
                "dropped_image_count",
                1,
            )

            return True

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")

        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(
        self,
    ) -> Iterator[sqlite3.Connection]:
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")

            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _row_to_item(
        row: sqlite3.Row,
    ) -> QueueItem:
        return QueueItem(
            capture_id=row["capture_id"],
            file_path=Path(row["file_path"]),
            captured_at_utc=row["captured_at_utc"],
            capture_source=row["capture_source"],
            file_size_bytes=int(row["file_size_bytes"]),
            sha256=row["sha256"],
            state=row["state"],
            retry_count=int(row["retry_count"]),
            next_attempt_at_utc=row["next_attempt_at_utc"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _set_state_in_connection(
        connection: sqlite3.Connection,
        key: str,
        value: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO agent_state (key, value)
            VALUES (?, ?)
            ON CONFLICT (key)
            DO UPDATE SET value = excluded.value
            """,
            (
                key,
                value,
            ),
        )

    @staticmethod
    def _get_state_int_in_connection(
        connection: sqlite3.Connection,
        key: str,
    ) -> int:
        row = connection.execute(
            """
            SELECT value
            FROM agent_state
            WHERE key = ?
            """,
            (key,),
        ).fetchone()

        if row is None:
            return 0

        return int(row["value"])

    def _increment_state_in_connection(
        self,
        connection: sqlite3.Connection,
        key: str,
        amount: int,
    ) -> None:
        value = (
            self._get_state_int_in_connection(
                connection,
                key,
            )
            + amount
        )

        self._set_state_in_connection(
            connection,
            key,
            str(value),
        )


def utc_now_text() -> str:
    return utc_datetime_text(datetime.now(UTC))


def utc_datetime_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
