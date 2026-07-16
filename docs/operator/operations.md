# Operations Runbook

This runbook covers daily operation of the API-hosted Telegram webhook, worker, Android device, exports, retention, and storage protection.

## Service status

```sh
systemctl status timelapse-camera.target --no-pager
systemctl status timelapse-api.service timelapse-worker.service --no-pager
```

Follow logs:

```sh
journalctl -u timelapse-api.service -f
journalctl -u timelapse-worker.service -f
```

## Health checks

Local and public liveness:

```sh
curl -fsS http://127.0.0.1:8100/health/live
curl -fsS https://camera.example.com/health/live
```

Telegram webhook handling runs inside `timelapse-api.service`. API startup automatically registers the public webhook and fails when Telegram registration fails.

Telegram:

```text
/help
/status
/latest front-door
```

Telegram user-facing timestamps are shown in Asia/Jakarta. User commands that include timestamps, such as `/images`, are entered in Asia/Jakarta and converted to UTC by the backend.

## Camera registration

Register a new Android camera without source changes:

```sh
sudo ./infrastructure/camera-admin.sh register-camera \
  --slug front-door \
  --display-name "Front Door"

sudo ./infrastructure/camera-admin.sh issue --camera front-door --valid-hours 8760
```

Update the Android `$HOME/timelapse/config.json`, then restart the Termux agent.

## Alerts

Health alerts are deduplicated through `alert_states`. Motion alerts send only the first image for a new grouped event. If Telegram is unavailable, workers preserve database state and retry pending work where applicable.

## Exports

Telegram commands:

```text
/images 2026-07-16 00:00 2026-07-16 23:59 front-door
/exports
/cancel <job-id>
```

Export ZIP parts are stored under `/srv/timelapse/exports` until delivery cleanup. Export ranges are half-open and limited to 24 hours.

## Retention and reconciliation

The worker runs retention and reconciliation loops:

- retention deletes expired eligible images while protecting active exports and pending/processing motion analyses;
- disk pressure rejects new uploads at the hard threshold and rejects new exports at severe pressure;
- emergency cleanup deletes oldest eligible scheduled images first;
- reconciliation detects missing files, checksum/size mismatches, orphaned files, stale staging rows, stale temp files, and old export files.

Check worker logs:

```sh
journalctl -u timelapse-worker.service -n 200 --no-pager
```

## Storage monitoring

```sh
df -h /srv/timelapse
find /srv/timelapse/images -type f | wc -l
find /srv/timelapse/exports -type f | wc -l
find /srv/timelapse/quarantine -type f | wc -l
```

If free space is near `STORAGE_SEVERE_MIN_FREE_BYTES`, review retention and exports before lowering thresholds.

## Restart procedures

```sh
sudo systemctl restart timelapse-api.service
sudo systemctl restart timelapse-worker.service
```

Restarting services is safe; workers use persistent state and idempotent claiming for health, motion, export, retention, and reconciliation work.
