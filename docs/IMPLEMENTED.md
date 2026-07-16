# Implemented Features

This document lists what is implemented in the current codebase. It is intended as a quick inventory for future contributors and agents before they inspect source files.

## Quality Gate and Tooling

Implemented:

- Python-only application code.
- Ruff lint and format configuration in `server/pyproject.toml` covers server and camera-agent code/tests.
- Server package metadata and dependencies in `server/pyproject.toml`.
- Camera-agent runtime dependencies in `camera-agent/requirements.txt`.
- Pytest unit and integration tests for server and camera-agent behavior.
- Integration tests skip safely when `TEST_DATABASE_URL` is not configured.

Important commands:

```sh
PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q
cd server && ../.venv/bin/pytest -q
.venv/bin/ruff check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests
.venv/bin/ruff format --check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests
```

## Android Camera Agent

Implemented under `camera-agent/src/camera_agent`:

- Termux runtime installer and self-test scripts.
- Runtime layout under `$HOME/timelapse` with copied `camera_agent` package and bin scripts.
- Termux:Boot integration script.
- Configuration loading from JSON.
- Scheduled capture loop.
- One-shot and count-based validation capture mode for `camera-self-test.sh`.
- `termux-camera-photo` invocation by configured camera ID.
- Raw capture validation.
- EXIF orientation handling, resize, and JPEG compression.
- Atomic pending-file write pattern.
- SQLite-backed upload queue.
- Queue recovery for interrupted uploads.
- Local queue cleanup by maximum bytes/age with dropped-image tracking.
- HTTP upload loop with retry/backoff.
- Heartbeat loop with queue, battery, storage, and capture/upload metrics.
- Validation command for capture evidence.

Tests cover:

- JPEG normalization and invalid capture rejection.
- Queue behavior and cleanup.
- Uploader retry/backoff behavior.
- Termux installer runtime import layout.
- Camera self-test CLI flags.
- Validation capture directory behavior.

## Server Foundation

Implemented under `server/src/timelapse`:

- FastAPI application with liveness endpoint.
- Pydantic settings from environment.
- SQLAlchemy asyncio database setup.
- Alembic migrations.
- PostgreSQL schema for cameras, credentials, images, heartbeats, health summaries, alert states, motion analyses/events, Telegram principals, exports, export parts, and audit events.
- Native systemd process entry points for API-hosted Telegram webhook, worker, and migrations.
- Structured process logging helpers.

## Camera Credentials and Authentication

Implemented:

- Camera registration CLI.
- Credential issue/list/revoke CLI.
- Token format with token ID plus secret.
- Server-side credential digest with pepper.
- Credential revocation and expiry fields.
- Constant-time secret comparison path.
- Per-camera authentication for image uploads and heartbeats.
- Rejection of invalid, revoked, and cross-camera credentials.

Operational helper:

```sh
sudo ./infrastructure/camera-admin.sh register-camera --slug front-door --display-name "Front Door"
sudo ./infrastructure/camera-admin.sh issue --camera front-door --valid-hours 8760
sudo ./infrastructure/camera-admin.sh revoke --token-id <token-id>
```

## Image Upload API

Implemented endpoint:

```http
POST /api/v1/cameras/{camera_slug}/images
```

Implemented behavior:

- Bearer credential authentication.
- Multipart upload validation.
- `capture_id` UUID parsing.
- `captured_at_utc` timestamp parsing and UTC conversion.
- Capture source validation.
- SHA-256 checksum validation.
- JPEG decode and dimension validation.
- 5 MiB upload limit.
- Server-generated storage paths; client filenames ignored.
- Atomic file installation to image storage.
- Idempotent duplicate upload response: `already_stored`.
- Motion analysis row creation for scheduled uploads.
- Camera last-seen/last-upload/last-capture updates.
- HTTP 507 upload guard at hard VPS disk-pressure threshold.

Tests cover valid upload, idempotency, checksum mismatch, invalid credential, revoked credential, cross-camera credential, oversized upload, invalid JPEG, and disk-pressure rejection.

## Heartbeat API and Health

Implemented endpoint:

```http
POST /api/v1/cameras/{camera_slug}/heartbeats
```

Implemented behavior:

- Authenticated heartbeat ingest.
- Persistence of device runtime, queue, battery, storage, last capture/upload, dropped count, capture failures, and last error.
- Camera `last_seen_at`, `last_capture_at`, and `last_upload_at` updates.
- Pure camera health classification: online, degraded, offline, disabled.
- Stable degraded/offline condition codes.
- Health worker evaluation loop.
- Persistent alert deduplication through `alert_states`.
- Offline/degraded/recovery Telegram alerts.
- Retry of pending health alerts when sender/recipient becomes available.
- Heartbeat daily aggregation.
- Detailed heartbeat expiry after aggregation.

Tests cover classification, worker persistence, alert deduplication, recovery alerting, Telegram failures, admin fallback, aggregation idempotency, and detailed-row expiry.

## Motion Detection and Alerts

Implemented:

- `frame-diff-v1` pure detector using OpenCV.
- Metrics for changed pixel ratio, largest region ratio, and brightness delta.
- Static scene suppression.
- Controlled motion detection.
- Lighting-change suppression.
- Structured skipped/failed analysis outcomes.
- Motion-analysis worker with safe claiming and stale recovery.
- Previous-image lookup from same camera.
- Five-minute motion event grouping.
- First-image Telegram photo alert for new motion events.
- No duplicate motion alert within a grouped event.
- Telegram failure does not delete/corrupt image or analysis records.
- Retry delivery for pending motion alerts.

Tests cover pure detector behavior, claiming/recovery, threaded detection execution, event grouping, alert success/failure, and image preservation on failures.

## Telegram Bot and Commands

Implemented:

- python-telegram-bot application hosted in the FastAPI lifespan.
- Secret-token-protected Telegram webhook endpoint.
- Automatic webhook registration during API startup.
- Fail-fast API startup when webhook registration fails.
- Authorization before command handling.
- Initial administrator bootstrap through `TELEGRAM_ADMIN_USER_ID` only.
- Generic unauthorized denial with no camera details.
- `/help` command.
- `/status [camera]` command.
- `/latest [camera]` command.
- `/images YYYY-MM-DD HH:mm YYYY-MM-DD HH:mm [camera]` command.
- `/exports` command.
- `/cancel <job-id>` command for administrators.
- Telegram user-facing timestamps formatted in Asia/Jakarta.
- Telegram `/images` input interpreted as Asia/Jakarta and converted to UTC before querying.

Tests cover authorization, admin bootstrap, command access, status/latest behavior, storage-path redaction, export command behavior, cancellation rules, and Jakarta timestamp formatting.

## Exports

Implemented:

- Strict `/images` date parser.
- Asia/Jakarta input conversion to UTC.
- Half-open export ranges: `[start, end)`.
- Maximum 24-hour export range.
- Snapshot rows in `export_job_images` to stabilize selected images.
- Export job listing by requesting user.
- Admin-only cancellation before Telegram upload begins.
- Export worker claiming.
- ZIP building from snapshot rows.
- CSV manifest inside ZIPs.
- Sequential ZIP parts targeting Telegram-safe 45 MiB limit.
- Stable failure when a single image cannot fit within the part-size limit.
- Durable state transitions for Telegram document upload.
- No resend of already-sent parts after interruption.
- Local deletion of sent parts.
- Job completion after all parts are deleted.
- Export rejection under severe/hard VPS disk pressure.

Tests cover parsing, snapshot determinism, locked image skip behavior, ZIP manifest, part limit, resume behavior, send/delete interruption, listing, cancellation, and disk pressure rejection.

## Daily Time-Lapse Videos

Implemented:

- One idempotent daily job per enabled camera for the previous Asia/Jakarta calendar day.
- Asia/Jakarta day conversion to a half-open UTC image range.
- Deterministic scheduled-image snapshot rows that defer rather than omit locked images.
- Recipient snapshots that avoid creating jobs when no Telegram destination exists.
- Stable `no_images` outcome for empty days.
- H.264 MP4 generation through system `ffmpeg` using ordered staged frames.
- Stale-aware worker leases that prevent concurrent processing while allowing crash recovery.
- Per-recipient Telegram delivery rows that prevent duplicate sends after partial failure.
- Automatic Telegram `sendVideo` delivery to enabled recipients with administrator fallback.
- Reuse of generated MP4 after transient Telegram delivery failure.
- Completion persisted before file cleanup so restart cleanup does not resend.
- Immediate MP4 deletion after successful delivery.
- Immediate oversized/failed artifact deletion with metadata and stable error code retained.
- Stable normalization of ffmpeg and filesystem errors.
- Severe/hard storage-pressure deferral with retained retry artifact cleanup.
- Active daily video snapshots protected from image retention using batched protection queries.

Tests cover Jakarta date boundaries, idempotent and lock-safe snapshots, empty/no-recipient days, ffmpeg command/output/error handling, PostgreSQL migration upgrade/downgrade, Telegram video transport, per-recipient partial retry, concurrent worker exclusion, send/delete behavior, delivery retry reuse, interrupted cleanup, storage pressure, oversized cleanup, and retention protection.

## Retention and Storage Protection

Implemented:

- Per-camera retention days.
- Retention worker deletion of expired eligible images.
- Active export and daily video snapshot protection.
- Pending/processing motion-analysis protection.
- Row locking with `FOR UPDATE SKIP LOCKED` for retention/export race safety.
- Missing file during retention treated as successful tombstone and audit.
- Filesystem deletion error restores image row to `stored` for retry.
- Disk pressure classification: normal, severe, hard limit.
- HTTP 507 upload rejection at hard threshold.
- New export rejection at severe/hard threshold.
- Emergency cleanup deleting oldest eligible scheduled images first.
- Emergency cleanup stops when pressure normalizes or no progress is possible.

Tests cover expired deletion, active export protection, locked row skip behavior, missing-file success, filesystem retry, pending analysis protection, disk pressure classification, upload 507, export rejection, and emergency cleanup ordering/no-progress behavior.

## Reconciliation

Implemented:

- Missing database file detection and image row marking.
- Orphaned file quarantine under `/srv/timelapse/quarantine/orphans`.
- Size/checksum mismatch detection and audit.
- Stale staging row marking.
- Stale staging file quarantine.
- Stale temporary upload file deletion.
- Old unreferenced export file deletion.
- Referenced export part protection.

Tests cover missing files, orphan quarantine, mismatch audit, stale temp/export cleanup, stale staging handling, and referenced export protection.

## Deployment and Operations

Implemented infrastructure:

- `bootstrap-ubuntu.sh` for Ubuntu 24.04 package/directory/firewall bootstrap.
- `deploy-systemd.sh` for release copy, package install, migrations, systemd/Nginx/Certbot configuration, and service restart.
- `verify-foundation.sh` for deployment foundation verification.
- Nginx HTTP and HTTPS templates.
- systemd units for API-hosted Telegram webhook, worker, migration, and target.
- `ffmpeg` installation and `/srv/timelapse/timelapses` storage preparation for daily videos.
- `camera-admin.sh` wrapper for installed credential CLI.
- `infrastructure/environment.example` with safe placeholders.

Implemented docs under `docs/operator/`:

- Android installation and validation.
- Server installation.
- Credential rotation.
- Operations runbook.
- Incident recovery.
- Acceptance coverage matrix.
- Soak-test report template.

## Current Acceptance Status

Completed locally:

- Server unit/integration tests pass with the configured test database.
- Camera-agent tests pass.
- Ruff check and format pass.
- Alembic head is current.
- Integration tests skip safely when no `TEST_DATABASE_URL` is configured.
- Operator documentation and acceptance coverage are present.

Still requires real environment execution:

- Fresh VPS deployment from docs/scripts.
- Android device registration against the deployed VPS.
- 24-hour MVP run.
- Seven-day soak test with no critical consistency defect.

## Known Intentional Constraints

- Production uses native systemd and a shared virtual environment, not Docker Compose.
- Database target is Neon PostgreSQL.
- Runtime uses Neon's pooled URL; migrations use the direct URL.
- Telegram operational messages are English only.
- User-facing Telegram timestamps are Asia/Jakarta.
- Backend storage and processing timestamps are UTC.
- Initial Telegram administrator access uses `TELEGRAM_ADMIN_USER_ID`; do not add `TELEGRAM_ADMIN_CHAT_ID` as a requirement.
- No public PostgreSQL listener.
- No long-running work in FastAPI request handlers.
