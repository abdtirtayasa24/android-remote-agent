# Credential Rotation

Camera credentials are bearer secrets. Store plaintext credentials only on the Android device and in the operator's secure password store. They are shown only when issued.

## Routine rotation with overlap

Use an overlap window so the Android phone can switch credentials before the old token is revoked.

1. Issue a new credential on the VPS:

   ```sh
   sudo ./infrastructure/camera-admin.sh issue \
     --camera front-door \
     --valid-hours 8760
   ```

2. Copy the new plaintext credential into the Android config:

   ```sh
   nano "$HOME/timelapse/config.json"
   chmod 600 "$HOME/timelapse/config.json"
   ```

3. Restart the Android agent:

   ```sh
   $HOME/timelapse/bin/start-agent.sh
   ```

4. Confirm uploads/heartbeats continue:

   ```sh
   sudo journalctl -u timelapse-api.service -n 100 --no-pager
   sudo ./infrastructure/camera-admin.sh list --camera front-door
   ```

5. Revoke the old token after the new credential is confirmed:

   ```sh
   sudo ./infrastructure/camera-admin.sh revoke --token-id <old-token-id>
   ```

## Emergency rotation after suspected leak

1. Immediately issue a replacement credential.
2. Update the Android device config.
3. Confirm the new credential works.
4. Revoke the exposed token ID.
5. Review logs for unexpected camera slug or IP activity.

## Listing credentials

```sh
sudo ./infrastructure/camera-admin.sh list --camera front-door
```

Use `last_used_at`, `expires_at`, and `revoked_at` to identify stale credentials.

## Rules

- Prefer short overlap windows.
- Do not paste credentials into issue trackers, logs, or Telegram.
- Do not commit Android `config.json` or `infrastructure/.env`.
- Keep `CAMERA_TOKEN_PEPPER` stable during normal credential rotation; changing it invalidates all stored credential digests.
