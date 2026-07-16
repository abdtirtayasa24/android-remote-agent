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
/speakcamera front-door
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

## Daily time-lapse videos

At 00:10 Asia/Jakarta by default, the worker snapshots the previous local calendar day's scheduled images for each enabled camera, generates an MP4 with `ffmpeg`, and sends it through Telegram. Successful MP4 files are deleted immediately; job status, size, checksum, and Telegram message ID remain in PostgreSQL.

Check processing and delivery failures in worker logs:

```sh
journalctl -u timelapse-worker.service -n 200 --no-pager | grep daily_timelapse
find /srv/timelapse/timelapses -type f -ls
```

A file may remain while Telegram delivery is retrying. Completed jobs are cleaned without resending after restart. Severe or hard storage pressure defers new generation and removes retained retry MP4s so daily videos cannot bypass disk protection.

## Voice-note playback

Select an enabled camera with `/speakcamera <camera-slug>`, then send the bot a Telegram voice note. The API only queues metadata; `timelapse-worker.service` downloads and normalizes the audio, and the Android agent claims it over the camera-authenticated command API.

Voice notes default to a 60-second/5 MiB limit, and unstarted commands expire after two minutes. A started command receives enough grace to finish playback. Server and Android audio files are deleted after completion, failure, or expiry. Inspect failures without exposing file IDs or credentials:

```sh
journalctl -u timelapse-worker.service -n 200 --no-pager | grep -E 'voice|camera_command'
find /srv/timelapse/audio-commands -type f -ls
```

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
find /srv/timelapse/timelapses -type f | wc -l
find /srv/timelapse/audio-commands -type f | wc -l
find /srv/timelapse/quarantine -type f | wc -l
```

If free space is near `STORAGE_SEVERE_MIN_FREE_BYTES`, review retention and exports before lowering thresholds.

## Restart procedures

```sh
sudo systemctl restart timelapse-api.service
sudo systemctl restart timelapse-worker.service
```

Restarting services is safe; workers use persistent state and idempotent claiming for health, motion, export, retention, and reconciliation work.
