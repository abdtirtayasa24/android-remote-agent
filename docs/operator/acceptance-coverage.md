# MVP Acceptance Coverage — Milestone 9

This document maps Must Have MVP behavior to Automated or Manual acceptance checks. It is the coverage index for Phase 5 / Milestone 9.

| Area | Must Have requirement | Coverage type | Evidence |
|---|---|---|---|
| Android capture | Phone captures valid JPEGs on a schedule | Automated + Manual | `camera-agent/tests`, `docs/operator/android-installation.md` ten-capture and 24-hour validation |
| Android queue | Offline images remain queued and upload later | Automated | `camera-agent/tests/test_queue.py`, `camera-agent/tests/test_uploader.py` |
| Android startup | Agent starts after reboot through Termux:Boot | Manual | `docs/operator/android-installation.md` reboot validation |
| Upload API | Authenticated camera can upload once idempotently | Automated | `server/tests/integration/test_image_upload.py` |
| Upload auth | Invalid, revoked, or cross-camera credentials are rejected | Automated | `server/tests/integration/test_image_upload.py`, `server/tests/unit/test_camera_credentials.py` |
| Heartbeats | Camera heartbeats persist health telemetry | Automated | `server/tests/integration/test_heartbeat.py` |
| Health state | Online/degraded/offline/disabled classification works | Automated | `server/tests/unit/test_health.py`, `server/tests/integration/test_health_worker.py` |
| Health alerts | Offline, degraded, and recovery alerts deduplicate | Automated | `server/tests/integration/test_health_worker.py` |
| Heartbeat retention | Daily heartbeat summaries and detailed expiry are idempotent | Automated | `server/tests/unit/test_heartbeat_aggregation.py`, `server/tests/integration/test_heartbeat_aggregation.py` |
| Motion detection | Static scene suppresses and controlled motion detects | Automated | `server/tests/unit/test_motion_detection.py` |
| Motion worker | Analysis claiming, stale recovery, and failure behavior are safe | Automated | `server/tests/integration/test_motion_worker.py` |
| Motion alerts | First image for a grouped event is sent only once | Automated | `server/tests/integration/test_motion_worker.py` |
| Telegram auth | Unauthorized users receive no camera details | Automated | `server/tests/unit/test_telegram_authorization.py`, `server/tests/integration/test_telegram_commands.py` |
| Telegram commands | `/help`, `/status`, `/latest` are authorized and safe | Automated | `server/tests/integration/test_telegram_commands.py` |
| Telegram timezone | User-facing Telegram times are Asia/Jakarta | Automated | `server/tests/unit/test_telegram_messages.py`, `server/tests/integration/test_telegram_commands.py` |
| Export request | `/images` accepts Asia/Jakarta input and snapshots UTC ranges | Automated | `server/tests/unit/test_export_date_parser.py`, `server/tests/integration/test_export_snapshot.py` |
| Export worker | ZIP manifest, part splitting, resume, and deletion are safe | Automated | `server/tests/unit/test_export_zip.py`, `server/tests/integration/test_export_worker.py` |
| Export operations | `/exports` and `/cancel` enforce ownership/admin rules | Automated | `server/tests/integration/test_export_commands.py` |
| Retention | Expired eligible images are deleted without breaking active exports | Automated | `server/tests/integration/test_retention.py` |
| Disk protection | Hard disk pressure rejects uploads and severe pressure rejects exports | Automated | `server/tests/unit/test_storage_pressure.py`, `server/tests/integration/test_disk_pressure.py` |
| Reconciliation | Missing, orphaned, mismatched, stale temp/export/staging files are handled | Automated | `server/tests/integration/test_reconciliation.py` |
| Native deployment | VPS uses systemd, Nginx, Certbot, shared venv, and Neon | Manual | `docs/operator/server-installation.md`, `infrastructure/*.sh` |
| Operations handover | Credential rotation, incidents, and runbooks are documented | Manual | `docs/operator/credential-rotation.md`, `docs/operator/operations.md`, `docs/operator/incident-recovery.md` |
| Soak test | 24-hour MVP and seven-day soak have no critical consistency defect | Manual | `docs/operator/soak-test-report.md` |

## Integration test skip behavior

Integration tests depend on `TEST_DATABASE_URL`. When it is not configured, `server/tests/integration/conftest.py` skips integration tests instead of using production data. Full integration verification must use a dedicated Neon test branch or database with `TEST_DATABASE_ALLOW_DESTRUCTIVE=true` when required.

## Regression focus

Regression tests cover these critical failure behaviors:

- duplicate upload idempotency;
- credential revocation and cross-camera credential rejection;
- Telegram unauthorized access;
- health alert deduplication and retry when recipients become available;
- motion detection failure without image deletion;
- export resume after send/delete interruptions;
- retention versus active exports and locked rows;
- disk pressure upload/export rejection;
- reconciliation of database/file mismatches.
