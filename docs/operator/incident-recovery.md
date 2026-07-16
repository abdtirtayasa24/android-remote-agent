# Incident Recovery

Use this guide when production behavior is inconsistent, storage is under pressure, exports fail, or database/file state diverges.

## First response

1. Preserve evidence before deleting files:

   ```sh
   date -u
   systemctl status timelapse-api.service timelapse-worker.service timelapse-bot.service --no-pager
   journalctl -u timelapse-api.service -n 200 --no-pager
   journalctl -u timelapse-worker.service -n 300 --no-pager
   df -h /srv/timelapse
   ```

2. Do not run destructive SQL manually unless explicitly approved.
3. Do not remove `/srv/timelapse/quarantine` until the incident is understood.

## Disk pressure

Symptoms:

- upload API returns HTTP 507 with `storage_pressure_hard_limit`;
- `/images` returns `storage_pressure_severe`;
- worker logs show emergency retention activity.

Actions:

```sh
df -h /srv/timelapse
find /srv/timelapse/exports -type f -mtime +1 -ls
find /srv/timelapse/quarantine -type f -ls
journalctl -u timelapse-worker.service -n 300 --no-pager
```

The worker deletes oldest eligible scheduled images first during emergency cleanup. Active exports and pending/processing analyses are protected. If space remains low, investigate large files under exports/quarantine and increase storage before deleting evidence.

## Database/file mismatch

The reconciliation worker detects:

- missing database files;
- orphaned files moved to quarantine;
- checksum or file-size mismatches;
- stale staging rows;
- stale temp files;
- old export files.

Review audit events in the database and quarantine contents:

```sh
find /srv/timelapse/quarantine -type f -ls
journalctl -u timelapse-worker.service -n 300 --no-pager
```

If an image file is missing but the database still references it, reconciliation marks it missing. If an unexpected file exists under images, reconciliation moves it to `/srv/timelapse/quarantine/orphans` before deletion.

## failed export

Symptoms:

- `/exports` shows a failed or stuck job;
- export parts remain under `/srv/timelapse/exports`;
- Telegram upload errors appear in worker logs.

Actions:

```sh
journalctl -u timelapse-worker.service -n 300 --no-pager
find /srv/timelapse/exports -type f -ls
```

Exports are resumable. A part marked sent is not resent; if local deletion fails, the next worker pass deletes the already-sent part. If upload has not begun, an administrator can cancel:

```text
/cancel <job-id>
```

If upload has begun, do not manually delete database rows. Preserve files and logs, then decide whether to let the worker retry or mark the incident for manual database repair.

## Telegram authorization issue

Initial administrator access uses `TELEGRAM_ADMIN_USER_ID`. Confirm the bot token and admin user ID in `/etc/android-remote/server.env`, then restart:

```sh
sudo systemctl restart timelapse-bot.service
journalctl -u timelapse-bot.service -n 100 --no-pager
```

Unauthorized users receive a generic denial and no camera details.

## Camera upload outage

1. Check Android agent status:

   ```sh
   $HOME/timelapse/bin/camera-self-test.sh status
   ```

2. Check server API logs:

   ```sh
   journalctl -u timelapse-api.service -n 200 --no-pager
   ```

3. Confirm credential state:

   ```sh
   sudo ./infrastructure/camera-admin.sh list --camera front-door
   ```

4. If credential leakage is suspected, follow `docs/operator/credential-rotation.md`.

## Recovery closure

Close an incident only after:

- services are healthy;
- disk pressure is below severe threshold;
- reconciliation has no new critical mismatch;
- exports are completed, cancelled, or documented;
- the root cause and follow-up action are recorded.
