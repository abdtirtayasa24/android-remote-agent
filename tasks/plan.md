# Implementation Plan: Continue Android Time-Lapse Security Camera MVP

## Overview
Continue from the implemented Milestones 1-4 toward the remaining MVP: camera health, motion detection, Telegram retrieval/export, and retention/storage protection. The current codebase already has the Android agent loops, secure image upload, heartbeats, baseline schema, native systemd deployment, and Neon PostgreSQL connectivity. The next work should start by making the quality gate reliable, then complete Milestone 5 before moving into motion and Telegram retrieval.

## Current Baseline
- Deployment is native systemd + shared Python virtualenv, not Docker Compose.
- Database target is Neon PostgreSQL with pooled runtime URL and direct migration URL.
- Implemented: camera credential CLI/auth, image upload/idempotency, heartbeat API/persistence, Android capture/upload/cleanup/heartbeat loops.
- Partially implemented: worker and bot process shells only; heartbeat state fields update, but no health state machine or alerts yet.
- Not implemented: health alerts, motion analysis, Telegram commands, export ZIPs, retention, disk pressure handling, reconciliation.

## Architecture Decisions
- Keep the existing systemd deployment model and update docs/specs whenever old Docker Compose assumptions appear.
- Build worker features as independently testable services first, then wire them into `timelapse.workers.application` loops.
- Keep expensive work out of FastAPI request handlers; API handlers should only validate, persist, and enqueue/snapshot.
- Prefer pure functions for health classification, frame differencing, date parsing, ZIP partitioning, and retention eligibility so they can be unit-tested without Telegram or a live database.
- Add a persistent `alert_states` table for health-condition and alert deduplication.
- Bootstrap Telegram administrator access from `TELEGRAM_ADMIN_USER_ID` only; do not require `TELEGRAM_ADMIN_CHAT_ID`.
- Treat heartbeat daily aggregation and detailed-row expiry as required Milestone 5 scope.
- Keep Telegram operational messages in English only.

## Dependency Graph

```text
Quality gate cleanup
    │
    ├── Health evaluation pure service
    │       ├── Health worker persistence
    │       └── Telegram alert delivery + dedupe
    │
    ├── Motion frame-diff pure service
    │       ├── Motion analysis worker
    │       └── Motion event grouping + first-image alert
    │
    ├── Telegram auth + command shell
    │       ├── /status and /latest
    │       └── /images snapshot creation
    │               └── Export worker ZIP delivery
    │
    └── Retention eligibility
            ├── Retention worker
            ├── Disk pressure behavior
            └── Reconciliation worker
```

## Task List

### Phase 0: Stabilize the Quality Gate
- [x] Task 1: Make existing lint/format gate pass without behavior changes

### Checkpoint: Preflight
- [x] Existing server unit tests pass
- [x] Existing camera-agent unit tests pass
- [x] Shared Ruff check and format check pass

### Phase 1: Milestone 5 — Camera Health
- [x] Task 2: Add health-state classification service
- [x] Task 3: Persist health state from the worker loop
- [x] Task 4: Add Telegram outbound client and alert formatting
- [x] Task 5: Implement health alert deduplication and audit trail
- [x] Task 6: Add heartbeat aggregation and expiry

### Checkpoint: Milestone 5
- [x] Offline is detected within 16 minutes in tests
- [x] Recovery is emitted once in tests
- [x] Degraded threshold transitions are tested
- [x] Worker can run continuously without crashing when database is available

### Phase 2: Milestone 6 — Motion Detection
- [x] Task 7: Implement `frame-diff-v1` as a pure motion detector
- [x] Task 8: Implement motion-analysis worker claiming and stale recovery
- [x] Task 9: Implement five-minute motion event grouping
- [x] Task 10: Send first-image Telegram motion alert

### Checkpoint: Milestone 6
- [x] Static scene does not alert in controlled tests
- [x] Person movement is detected in controlled tests
- [x] Multiple detections within five minutes produce one alert
- [x] Failed analysis never deletes or corrupts the image row

### Phase 3: Milestone 7 — Telegram Retrieval and Exports
- [ ] Task 11: Implement Telegram authorization middleware and bot bootstrap
- [ ] Task 12: Implement `/help`, `/status`, and `/latest`
- [ ] Task 13: Implement strict `/images` date parser and export snapshot creation
- [ ] Task 14: Implement export worker ZIP parts and manifest
- [ ] Task 15: Implement `/exports` and `/cancel`

### Checkpoint: Milestone 7
- [ ] Unauthorized Telegram users receive no camera details
- [ ] Asia/Jakarta date parsing handles cross-midnight ranges
- [ ] Exact 24-hour ranges pass; over-24-hour ranges fail
- [ ] ZIP manifest exactly matches `export_job_images`
- [ ] Interrupted export resumes at the first unsent part

### Phase 4: Milestone 8 — Retention and Storage Protection
- [ ] Task 16: Implement retention eligibility and deletion worker
- [ ] Task 17: Implement disk pressure checks and HTTP 507 upload guard
- [ ] Task 18: Implement filesystem reconciliation worker

### Checkpoint: Milestone 8
- [ ] Expired images are removed while active-export images remain
- [ ] Low disk state rejects new uploads safely at the hard threshold
- [ ] Missing files, orphaned files, checksum mismatches, old staging rows, and stale temp files are detected

### Phase 5: Milestone 9 — QA and Handover
- [ ] Task 19: Complete automated coverage for MVP acceptance scenarios
- [ ] Task 20: Update operations, credential rotation, and incident recovery docs
- [ ] Task 21: Run 24-hour MVP and seven-day soak-test checklist

### Checkpoint: Complete MVP
- [ ] All relevant unit/integration tests pass
- [ ] Shared Ruff check and format check pass
- [ ] Fresh VPS deployment succeeds from docs/scripts
- [ ] Android device can be registered without source changes
- [ ] Seven-day soak test passes with no critical consistency defect

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Alert deduplication needs persistent per-condition state not present in the baseline schema | High | Add a dedicated `alert_states` table in Milestone 5. |
| Telegram Bot API failures can interrupt alerts/exports | Medium | Persist delivery attempts and make worker retries idempotent. |
| Motion thresholds produce false alerts in real lighting conditions | Medium | Keep frame-diff thresholds configurable per camera and log analysis metrics for tuning. |
| Export ZIPs can race with retention | High | Use existing `export_job_images` snapshot and exclude active export references during retention. |
| Neon test database access may be unavailable locally | Medium | Keep pure unit tests broad; mark integration tests skipped unless `TEST_DATABASE_URL` is configured. |
| Current Ruff gate fails before new work | Medium | Start with a behavior-preserving lint/format cleanup task. |

## Resolved Decisions

- A small persistent `alert_states` table is allowed and should be used for alert deduplication.
- Initial Telegram administrator provisioning uses `TELEGRAM_ADMIN_USER_ID`; `TELEGRAM_ADMIN_CHAT_ID` is not required.
- Heartbeat daily aggregation and detailed-row expiry are required in Milestone 5.
- Telegram operational messages should be English only.

## Parallelization Opportunities

- After Task 1, Tasks 2 and 7 can be developed independently because they are pure services.
- After Telegram authorization contracts are defined, `/status`/`/latest` and export worker ZIP generation can proceed in parallel.
- Retention eligibility tests can be written in parallel with export worker work once `export_job_images` snapshot behavior is fixed.

## Approval Gate

Open planning questions from this pass are resolved. Implementation can start with Task 1, then Milestone 5.
