# Task List: Continue Android Time-Lapse Security Camera MVP

## Task 1: Make existing lint/format gate pass without behavior changes ✅

**Description:** Clean up current Ruff and format violations in server and camera-agent code/tests without changing runtime behavior.

**Acceptance criteria:**
- [x] Ruff check passes for `server/src server/tests camera-agent/src camera-agent/tests`.
- [x] Ruff format check passes for the same paths.
- [x] Existing tests still pass.

**Verification:**
- [x] `PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q`
- [x] `cd server && ../.venv/bin/pytest tests/unit -q`
- [x] `.venv/bin/ruff check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests`
- [x] `.venv/bin/ruff format --check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests`

**Dependencies:** None

**Files likely touched:**
- `camera-agent/src/camera_agent/*.py`
- `camera-agent/tests/*.py`
- `server/tests/**/*.py`

**Estimated scope:** Medium

---

## Task 2: Add health-state classification service ✅

**Description:** Implement a pure service that classifies a camera as online, degraded, offline, or disabled based on current time, camera fields, latest heartbeat/upload information, and configured thresholds.

**Acceptance criteria:**
- [x] Online, degraded, offline, and disabled classifications match the spec.
- [x] All degraded conditions are represented with stable condition codes.
- [x] The service is deterministic and unit-testable without a database.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit -q -k health`

**Dependencies:** Task 1

**Files likely touched:**
- `server/src/timelapse/services/health.py`
- `server/tests/unit/test_health.py`

**Estimated scope:** Small

---

## Task 3: Persist health state from the worker loop ✅

**Description:** Add a worker loop that periodically evaluates cameras, updates `cameras.health_state`, and records state changes without sending Telegram alerts yet.

**Acceptance criteria:**
- [x] Worker updates camera health state based on heartbeat/upload age.
- [x] Disabled cameras remain disabled.
- [x] State transitions are committed atomically and safe to rerun.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k health` with `TEST_DATABASE_URL` configured
- [x] Unit-only health tests pass when no test database is configured

**Dependencies:** Task 2

**Files likely touched:**
- `server/src/timelapse/workers/application.py`
- `server/src/timelapse/services/health.py`
- `server/tests/integration/test_health_worker.py`

**Estimated scope:** Medium

---

## Task 4: Add Telegram outbound client and alert formatting ✅

**Description:** Add a minimal Telegram Bot API client and formatting utilities for health and motion alerts. This task should not wire alerts into workers yet.

**Acceptance criteria:**
- [x] Client sends messages and photos using `TELEGRAM_BOT_TOKEN` without logging secrets.
- [x] Formatting produces English-only, generic unauthorized-safe messages with no storage paths.
- [x] Client is mockable in tests.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit -q -k telegram`

**Dependencies:** Task 1

**Files likely touched:**
- `server/src/timelapse/services/telegram_client.py`
- `server/src/timelapse/services/telegram_messages.py`
- `server/src/timelapse/configuration.py`
- `server/tests/unit/test_telegram_messages.py`

**Estimated scope:** Medium

---

## Task 5: Implement health alert deduplication and audit trail ✅

**Description:** Emit Telegram offline, recovery, and degraded alerts only on transitions, and record alert attempts/results for auditability.

**Acceptance criteria:**
- [x] Offline alert is sent once per offline transition.
- [x] Recovery alert is sent once per recovery transition.
- [x] Repeated unchanged degraded conditions do not duplicate alerts.
- [x] Alert outcomes are recorded without storing secrets.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k health` with `TEST_DATABASE_URL` configured

**Dependencies:** Tasks 3 and 4

**Files likely touched:**
- `server/src/timelapse/services/health.py`
- `server/src/timelapse/services/telegram_client.py`
- `server/src/timelapse/models/entities.py`
- `server/migrations/versions/*`
- `server/tests/integration/test_health_alerts.py`

**Estimated scope:** Medium

---

## Task 6: Add heartbeat aggregation and expiry ✅

**Description:** Summarize heartbeat history before detailed rows expire, then delete old detailed heartbeat rows safely.

**Acceptance criteria:**
- [x] Daily aggregate values include heartbeat count, min battery, max temperature, max queue size, and offline duration where data allows.
- [x] Detailed rows older than retention are deleted after aggregation.
- [x] Aggregation is idempotent.

**Verification:**
- [x] Unit-only heartbeat aggregation tests pass when no test database is configured
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k heartbeat` with `TEST_DATABASE_URL` configured

**Dependencies:** Task 3

**Files likely touched:**
- `server/src/timelapse/services/heartbeat_aggregation.py`
- `server/src/timelapse/workers/application.py`
- `server/src/timelapse/models/entities.py`
- `server/migrations/versions/*`
- `server/tests/integration/test_heartbeat_aggregation.py`

**Estimated scope:** Medium

---

## Task 7: Implement `frame-diff-v1` as a pure motion detector ✅

**Description:** Implement the OpenCV frame-difference algorithm as a pure function that accepts image paths/config thresholds and returns metrics plus detection/suppression status.

**Acceptance criteria:**
- [x] Static scene returns no motion.
- [x] Controlled movement returns motion.
- [x] Large brightness shift can be suppressed as lighting change.
- [x] Missing/invalid image inputs return a structured skip/failure result.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit -q -k motion`

**Dependencies:** Task 1

**Files likely touched:**
- `server/src/timelapse/services/motion_detection.py`
- `server/tests/unit/test_motion_detection.py`

**Estimated scope:** Medium

---

## Task 8: Implement motion-analysis worker claiming and stale recovery ✅

**Description:** Process pending `motion_analyses` rows with safe claiming, update status/metrics, and recover stale processing rows.

**Acceptance criteria:**
- [x] Pending analyses are claimed without double processing.
- [x] Completed analyses store metrics and motion result.
- [x] Failed/skipped analyses keep the image row intact.
- [x] Stale processing rows are reclaimable.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k motion` with `TEST_DATABASE_URL` configured

**Dependencies:** Task 7

**Files likely touched:**
- `server/src/timelapse/services/motion_worker.py`
- `server/src/timelapse/workers/application.py`
- `server/tests/integration/test_motion_worker.py`

**Estimated scope:** Medium

---

## Task 9: Implement five-minute motion event grouping ✅

**Description:** Group motion detections into open events and append detections within the configured five-minute window.

**Acceptance criteria:**
- [x] First detection creates a motion event.
- [x] Detection within five minutes appends to the event and does not request a second alert.
- [x] New detection after the window creates a new event.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/integration -q -k motion` with `TEST_DATABASE_URL` configured

**Dependencies:** Task 8

**Files likely touched:**
- `server/src/timelapse/services/motion_events.py`
- `server/src/timelapse/services/motion_worker.py`
- `server/tests/integration/test_motion_events.py`

**Estimated scope:** Medium

---

## Task 10: Send first-image Telegram motion alert ✅

**Description:** Send only the first detected image for a new motion event to authorized Telegram recipients.

**Acceptance criteria:**
- [x] New motion event queues/sends one photo alert.
- [x] Detections appended within five minutes do not send another photo.
- [x] Telegram failure does not fail or delete motion analysis/image records.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k motion` with `TEST_DATABASE_URL` configured

**Dependencies:** Tasks 4 and 9

**Files likely touched:**
- `server/src/timelapse/services/motion_events.py`
- `server/src/timelapse/services/telegram_client.py`
- `server/tests/integration/test_motion_alerts.py`

**Estimated scope:** Medium

---

## Task 11: Implement Telegram authorization middleware and bot bootstrap ✅

**Description:** Replace the bot foundation shell with a python-telegram-bot application that authorizes Telegram user IDs before command handling. Bootstrap the administrator from `TELEGRAM_ADMIN_USER_ID`; do not require `TELEGRAM_ADMIN_CHAT_ID`.

**Acceptance criteria:**
- [x] Unauthorized users receive a generic denial.
- [x] The `TELEGRAM_ADMIN_USER_ID` user is authorized as administrator without a chat ID env var.
- [x] Authorized users can reach command handlers.
- [x] No camera details are exposed before authorization.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit -q -k telegram_auth`

**Dependencies:** Task 1

**Files likely touched:**
- `server/src/timelapse/bot/application.py`
- `server/src/timelapse/bot/authorization.py`
- `server/src/timelapse/configuration.py`
- `server/src/timelapse/models/entities.py`
- `server/migrations/versions/*`
- `server/tests/unit/test_telegram_authorization.py`

**Estimated scope:** Medium

---

## Task 12: Implement `/help`, `/status`, and `/latest` ✅

**Description:** Add read-only Telegram commands for help text, camera health summary, and latest stored image retrieval.

**Acceptance criteria:**
- [x] `/help` lists supported commands for the authorized role.
- [x] `/status [camera]` returns health and queue summary without storage paths.
- [x] `/latest [camera]` sends the latest stored image for an authorized user.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k telegram_commands` with `TEST_DATABASE_URL` configured

**Dependencies:** Task 11

**Files likely touched:**
- `server/src/timelapse/bot/commands.py`
- `server/src/timelapse/services/camera_status.py`
- `server/tests/unit/test_telegram_commands.py`

**Estimated scope:** Medium

---

## Task 13: Implement strict `/images` date parser and export snapshot creation ✅

**Description:** Parse `/images YYYY-MM-DD HH:mm YYYY-MM-DD HH:mm [camera]` in Asia/Jakarta, enforce half-open intervals and 24-hour limit, then snapshot selected image IDs into export job tables.

**Acceptance criteria:**
- [x] Invalid formats are rejected with usage guidance.
- [x] Exact 24-hour range is accepted; over-24-hour range is rejected.
- [x] Cross-midnight Asia/Jakarta range converts correctly to UTC.
- [x] Snapshot rows are deterministic and stable.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k export_request` with `TEST_DATABASE_URL` configured

**Dependencies:** Task 11

**Files likely touched:**
- `server/src/timelapse/bot/date_parser.py`
- `server/src/timelapse/services/export_requests.py`
- `server/tests/unit/test_export_date_parser.py`
- `server/tests/integration/test_export_snapshot.py`

**Estimated scope:** Medium

---

## Task 14: Implement export worker ZIP parts and manifest ✅

**Description:** Build ZIP files from `export_job_images`, include a CSV manifest, split parts at the 45 MiB Telegram-safe limit, send sequentially, and resume after restart.

**Acceptance criteria:**
- [x] Every snapshot image appears exactly once in the manifest.
- [x] No image outside the snapshot is included.
- [x] ZIP parts are no larger than 45 MiB.
- [x] Restart resumes at the first unsent part.
- [x] Sent parts are deleted locally.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k export_worker` with `TEST_DATABASE_URL` configured

**Dependencies:** Tasks 4 and 13

**Files likely touched:**
- `server/src/timelapse/services/export_worker.py`
- `server/src/timelapse/services/export_zip.py`
- `server/src/timelapse/workers/application.py`
- `server/tests/unit/test_export_zip.py`
- `server/tests/integration/test_export_worker.py`

**Estimated scope:** Medium

---

## Task 15: Implement `/exports` and `/cancel` ✅

**Description:** Add Telegram commands to list a user's five most recent export jobs and cancel an export that has not begun Telegram upload.

**Acceptance criteria:**
- [x] `/exports` lists only the requesting user's jobs.
- [x] `/cancel <job-id>` requires administrator role.
- [x] Cancellation is rejected once Telegram upload has begun.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k export_commands` with `TEST_DATABASE_URL` configured

**Dependencies:** Tasks 11 and 13

**Files likely touched:**
- `server/src/timelapse/bot/commands.py`
- `server/src/timelapse/services/export_requests.py`
- `server/tests/unit/test_export_commands.py`

**Estimated scope:** Small

---

## Task 16: Implement retention eligibility and deletion worker

**Description:** Delete expired images in batches while excluding active exports, pending/processing analysis, and non-stored rows.

**Acceptance criteria:**
- [ ] Expired eligible rows are marked deleting, files removed, and metadata removed/tombstoned consistently.
- [ ] Active-export rows are never deleted.
- [ ] Missing files are treated as successful deletion and audited.
- [ ] Filesystem errors restore rows to stored for retry.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k retention` when `TEST_DATABASE_URL` is configured

**Dependencies:** Task 13

**Files likely touched:**
- `server/src/timelapse/services/retention.py`
- `server/src/timelapse/workers/application.py`
- `server/tests/integration/test_retention.py`

**Estimated scope:** Medium

---

## Task 17: Implement disk pressure checks and HTTP 507 upload guard

**Description:** Add storage free-space checks to reject uploads at the hard threshold and trigger emergency cleanup policy for disk pressure.

**Acceptance criteria:**
- [ ] Upload API returns HTTP 507 when free space is below the hard threshold.
- [ ] New exports are rejected at severe disk pressure.
- [ ] Emergency cleanup deletes oldest eligible scheduled images first.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k disk_pressure` when `TEST_DATABASE_URL` is configured

**Dependencies:** Task 16

**Files likely touched:**
- `server/src/timelapse/services/storage_pressure.py`
- `server/src/timelapse/api/images.py`
- `server/src/timelapse/services/retention.py`
- `server/tests/unit/test_storage_pressure.py`
- `server/tests/integration/test_disk_pressure.py`

**Estimated scope:** Medium

---

## Task 18: Implement filesystem reconciliation worker

**Description:** Detect missing files, orphaned files, incorrect sizes, checksum mismatches, stale staging rows, stale temp files, and old export files.

**Acceptance criteria:**
- [ ] Missing database files are marked/audited.
- [ ] Orphaned files move to quarantine before deletion.
- [ ] Checksum and file-size mismatches are detected.
- [ ] Stale staging/temp/export files are handled according to spec.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit tests/integration -q -k reconciliation` when `TEST_DATABASE_URL` is configured

**Dependencies:** Task 16

**Files likely touched:**
- `server/src/timelapse/services/reconciliation.py`
- `server/src/timelapse/workers/application.py`
- `server/tests/integration/test_reconciliation.py`

**Estimated scope:** Medium

---

## Task 19: Complete automated coverage for MVP acceptance scenarios

**Description:** Fill remaining automated test gaps from the spec's minimum test suite, prioritizing auth, exports, retention, motion, and worker recovery.

**Acceptance criteria:**
- [ ] Each Must Have requirement has at least one automated or documented manual acceptance check.
- [ ] Integration tests skip safely when `TEST_DATABASE_URL` is missing.
- [ ] Regression tests cover all implemented critical failure behaviors.

**Verification:**
- [ ] `PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q`
- [ ] `cd server && ../.venv/bin/pytest -q`

**Dependencies:** Tasks 1-18

**Files likely touched:**
- `server/tests/**/*`
- `camera-agent/tests/**/*`
- `docs/SPEC-1-Android-Time-Lapse-Security-Camera.md`

**Estimated scope:** Medium

---

## Task 20: Update operations, credential rotation, and incident recovery docs

**Description:** Add operator documentation for systemd deployment, camera registration, Telegram principal setup, credential rotation, alerts, exports, retention, and recovery procedures.

**Acceptance criteria:**
- [ ] Fresh VPS deployment can be followed from docs.
- [ ] New Android phone registration requires no source changes.
- [ ] Credential rotation procedure preserves overlap and revocation.
- [ ] Incident recovery covers DB/file mismatch, disk pressure, and failed exports.

**Verification:**
- [ ] Manual documentation review against scripts and commands

**Dependencies:** Tasks 5, 11, 14, 16, 18

**Files likely touched:**
- `README.md`
- `docs/android-installation.md`
- `docs/server-installation.md`
- `docs/credential-rotation.md`
- `docs/operations.md`
- `docs/incident-recovery.md`

**Estimated scope:** Medium

---

## Task 21: Run 24-hour MVP and seven-day soak-test checklist

**Description:** Execute and record the operational acceptance tests, including Android reboot, Wi-Fi interruption, VPS service restart, motion generation, exports, and retention boundary checks.

**Acceptance criteria:**
- [ ] 24-hour MVP test passes or defects are documented with severity.
- [ ] Seven-day soak test passes before final MVP acceptance.
- [ ] No critical consistency, unauthorized access, unrecoverable job, or duplicate capture defect remains unresolved.

**Verification:**
- [ ] Documented soak-test report with commands, timestamps, and results

**Dependencies:** Tasks 1-20

**Files likely touched:**
- `docs/soak-test-report.md`
- `docs/SPEC-1-Android-Time-Lapse-Security-Camera.md`

**Estimated scope:** Medium
