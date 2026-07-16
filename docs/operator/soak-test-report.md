# Soak Test Report

This document records the 24-hour MVP run and seven-day soak. Fill in real timestamps and evidence paths during execution.

## Summary

| Item | Status | Evidence |
|---|---|---|
| 24-hour MVP | Not yet run | Pending |
| seven-day soak | Not yet run | Pending |
| critical consistency defects | None open before run | Pending confirmation |
| unauthorized access defects | None open before run | Pending confirmation |
| unrecoverable job defects | None open before run | Pending confirmation |
| duplicate capture defects | None open before run | Pending confirmation |

## Environment

- VPS host: `TODO`
- Public domain: `TODO`
- Camera slug: `TODO`
- Android model/version: `TODO`
- Termux source/version: `TODO`
- Server commit: `TODO`
- Started at UTC: `TODO`
- Started at Asia/Jakarta: `TODO`

## 24-hour MVP checklist

Run these checks and paste command output or evidence file paths.

### Server preflight

```sh
sudo ./infrastructure/verify-foundation.sh
systemctl status timelapse-api.service timelapse-worker.service --no-pager
curl -fsS https://camera.example.com/health/live
```

Expected: all services active and liveness returns `{"status":"ok"}`.

### Android capture and reboot

```sh
$HOME/timelapse/bin/camera-self-test.sh info
$HOME/timelapse/bin/camera-self-test.sh ten 0
$HOME/timelapse/bin/camera-self-test.sh status
```

Reboot Android, wait several minutes, then:

```sh
$HOME/timelapse/bin/camera-self-test.sh status
```

Expected: Termux:Boot starts the agent and new captures continue.

### Wi-Fi interruption

1. Disable Wi-Fi/mobile data on Android for at least five minutes.
2. Re-enable network.
3. Confirm queued images upload and the server receives heartbeats.

Evidence:

```sh
journalctl -u timelapse-api.service -n 200 --no-pager
journalctl -u timelapse-worker.service -n 200 --no-pager
```

### Telegram operations

Send from the authorized admin account:

```text
/help
/status
/latest front-door
/images YYYY-MM-DD HH:mm YYYY-MM-DD HH:mm front-door
/exports
```

Expected: timestamps shown to users are Asia/Jakarta. Export input timestamps are Asia/Jakarta and the backend converts to UTC internally.

### Motion generation

Create controlled motion in the camera scene.

Expected:

- one motion alert for the first image in a five-minute group;
- no duplicate alerts within the cooldown window;
- image rows remain stored.

### Retention boundary

Confirm retention is active but does not remove active export images:

```sh
journalctl -u timelapse-worker.service -n 300 --no-pager
find /srv/timelapse/images -type f | wc -l
find /srv/timelapse/exports -type f | wc -l
```

### Disk protection and reconciliation

```sh
df -h /srv/timelapse
find /srv/timelapse/quarantine -type f -ls
journalctl -u timelapse-worker.service -n 300 --no-pager
```

Expected: no uninvestigated quarantine growth, no repeated reconciliation failures, and no hard disk pressure.

## Seven-day soak checklist

Repeat the daily checks for seven days:

| Day | Server services healthy | Android agent running | Heartbeats current | Motion sane | Exports sane | Disk sane | Notes |
|---|---|---|---|---|---|---|---|
| 1 | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 2 | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 3 | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 4 | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 5 | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 6 | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| 7 | TODO | TODO | TODO | TODO | TODO | TODO | TODO |

## Defect log

| Time UTC | Severity | Area | Description | Mitigation | Status |
|---|---|---|---|---|---|
| TODO | TODO | TODO | TODO | TODO | TODO |

Severity guidance:

- Critical: consistency defect, unauthorized access, unrecoverable job, or duplicate capture defect.
- High: data loss risk with a known recovery path.
- Medium: operational degradation with workaround.
- Low: documentation or observability gap.

## Acceptance decision

The MVP is accepted only when:

- the 24-hour MVP checklist passes or non-critical defects are documented;
- the seven-day soak passes;
- no critical consistency defect remains open;
- no unauthorized access defect remains open;
- no unrecoverable job defect remains open;
- no duplicate capture defect remains open.
