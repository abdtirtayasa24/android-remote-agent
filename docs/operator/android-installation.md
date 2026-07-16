# Android Camera Installation and Milestone 1 Validation

## Scope

This guide validates the Android camera hardware and long-running Termux environment.

It does not configure the VPS, upload images, create an SQLite queue, or send heartbeats.

## Prerequisites

* Android 9 or newer
* No root access required
* Termux
* Termux:API Android add-on
* Termux:Boot Android add-on
* A suitable continuous charger
* The `camera-agent` repository directory copied or cloned onto the phone

Install Termux and its add-ons from the same distribution source. Mixing differently signed applications can prevent the add-ons from communicating with Termux.

Open each application once after installation.

Grant the camera permission to Termux:API and any notification permission requested by Termux.

Exclude Termux, Termux:API, and Termux:Boot from Android battery optimization.

## Install the Milestone 1 agent

From the repository root in Termux:

```sh
cd camera-agent
chmod 700 scripts/*.sh
./scripts/install-termux.sh
```

The installer creates:

```text
$HOME/timelapse/
├── app/camera_agent/
├── bin/
├── config.json
├── logs/
├── run/
└── validation-captures/
```

Install boot integration when the runtime layout is ready:

```sh
./scripts/install-boot-script.sh
```

That creates:

```text
$HOME/.termux/boot/10-start-camera-agent
```

## Inspect available cameras

Run:

```sh
$HOME/timelapse/bin/camera-self-test.sh info
```

The command records its output at:

```text
$HOME/timelapse/logs/milestone-1/camera-info.json
```

Camera IDs are usually numeric. Do not assume that camera `0` is the desired rear camera; test the IDs reported by the phone.

## Test candidate camera IDs

For each candidate camera ID:

```sh
$HOME/timelapse/bin/camera-self-test.sh once 0
```

Replace `0` with the camera ID being tested.

Inspect the newest file:

```sh
find "$HOME/timelapse/validation-captures" \
  -type f -name '*.jpg' |
  sort |
  tail -n 1
```

Open it through Android:

```sh
termux-open "$(find "$HOME/timelapse/validation-captures" \
  -type f -name '*.jpg' |
  sort |
  tail -n 1)"
```

Confirm that:

* The selected lens is correct.
* The image is not black.
* The image is correctly rotated.
* The scene is in focus.
* The image dimensions do not exceed 1280×720.

Update the selected ID:

```sh
nano "$HOME/timelapse/config.json"
```

Keep the normal values:

```json
{
  "capture_interval_seconds": 60,
  "maximum_width": 1280,
  "maximum_height": 720,
  "jpeg_quality": 72
}
```

## Run the ten-capture acceptance test

Run:

```sh
$HOME/timelapse/bin/camera-self-test.sh ten 0
```

Replace `0` with the selected camera ID.

The test takes approximately nine minutes between the first and tenth capture.

It passes only when:

* Ten images are produced.
* Every image is non-empty.
* Every image decodes as JPEG.
* Every image fits within 1280×720.
* No gap between consecutive captures exceeds 90 seconds.
* No capture command fails.

The report is stored at:

```text
$HOME/timelapse/logs/milestone-1/ten-capture-report.json
```

A passing report contains:

```json
{
  "valid_capture_count": 10,
  "invalid_capture_count": 0
}
```

## Screen-off validation

Start the long-running agent:

```sh
$HOME/timelapse/bin/start-agent.sh
```

Alternatively, reboot the phone after completing the Termux:Boot setup.

Record the screen-off test start time:

```sh
date -u +%Y-%m-%dT%H:%M:%SZ
```

Turn the screen off using the normal Android power button.

Leave the device untouched for at least ten minutes.

Turn the screen on and run:

```sh
$HOME/timelapse/bin/camera-self-test.sh status
```

Validate captures created after the recorded UTC time:

```sh
$HOME/timelapse/bin/camera-self-test.sh \
  validate 2026-07-14T10:00:00Z 10
```

Replace the example timestamp with the recorded value.

The screen-off test passes when the agent remains running and valid images continue to appear while the screen is off.

## Reboot validation

Open the Termux:Boot application once from its launcher before testing.

Confirm that the boot script exists:

```sh
ls -l "$HOME/.termux/boot/10-start-camera-agent"
```

Reboot the phone.

Do not manually open a Termux session immediately after reboot. Allow Android several minutes to complete startup.

Open Termux and run:

```sh
$HOME/timelapse/bin/camera-self-test.sh status
```

Inspect the agent startup record:

```sh
grep agent_started "$HOME/timelapse/logs/camera-agent.log" |
tail -n 5
```

The reboot test passes when:

* The PID status reports a running process.
* A new `agent_started` line exists after the reboot.
* New JPEG files appear without manually launching the Python process.

## Twenty-four-hour thermal and stability validation

Keep the phone attached to its intended charger and in its intended mounting position.

Start the agent through Termux:Boot or:

```sh
$HOME/timelapse/bin/start-agent.sh
```

In another Termux session, start thermal evidence collection:

```sh
$HOME/timelapse/bin/camera-self-test.sh thermal 24
```

The thermal command records battery data every five minutes for 24 hours.

Record the run start:

```sh
date -u +%Y-%m-%dT%H:%M:%SZ |
tee "$HOME/timelapse/logs/milestone-1/soak-started-at-utc.txt"
```

After 24 hours, check the process:

```sh
$HOME/timelapse/bin/camera-self-test.sh status
```

Validate the evidence:

```sh
started_at="$(
  cat "$HOME/timelapse/logs/milestone-1/soak-started-at-utc.txt"
)"

$HOME/timelapse/bin/camera-self-test.sh \
  validate "$started_at" 10 |
tee "$HOME/timelapse/logs/milestone-1/24-hour-capture-report.json"
```

Review the number of valid captures. A continuous 60-second schedule should produce approximately 1,440 images in 24 hours, subject to the exact start and stop times.

Review capture failures:

```sh
grep -c capture_failed "$HOME/timelapse/logs/camera-agent.log"
```

Review skipped slots:

```sh
grep -c capture_slots_skipped "$HOME/timelapse/logs/camera-agent.log"
```

Review the thermal log under:

```text
$HOME/timelapse/logs/milestone-1/
```

The 24-hour validation passes when:

* The agent remains alive.
* No unhandled exception terminates the scheduler.
* Captures continue while the screen is off.
* The device does not enter thermal shutdown.
* The battery does not discharge continuously while connected to the intended charger.
* Images remain valid JPEG files within 1280×720.
* Any failures or scheduling gaps are documented.

Stop a manually started foreground process with `Ctrl+C`.

## Local automated test

From the repository root, using the project virtual environment:

```sh
PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q
```

Expected result: all camera-agent tests pass.

## Required Milestone 1 evidence

Keep these records:

```text
$HOME/timelapse/logs/milestone-1/camera-info.json
$HOME/timelapse/logs/milestone-1/ten-capture-report.json
$HOME/timelapse/logs/milestone-1/soak-started-at-utc.txt
$HOME/timelapse/logs/milestone-1/24-hour-capture-report.json
$HOME/timelapse/logs/milestone-1/thermal-*.log
$HOME/timelapse/logs/camera-agent.log
```

Milestone 1 is complete only after all four acceptance conditions pass:

1. Ten consecutive valid captures
2. Screen-off capture works
3. Agent starts after reboot
4. Twenty-four-hour run without agent termination
