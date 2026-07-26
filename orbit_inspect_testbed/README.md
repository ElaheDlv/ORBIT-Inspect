# ORBIT-Inspect Interactive Testbed

This folder contains the ORBIT-Inspect demo path. Use these files for the interactive
testbed; do not edit the original root-level experiment scripts for demo
changes.

For a from-scratch setup of the external Isaac/OAI/FlexRIC/Docker stack, see
[`SETUP_0_TO_100.md`](SETUP_0_TO_100.md). Keep this README focused on operating
the already-installed demo.

## What This Demo Controls

The live dashboard controls the same research knobs used by the measured
experiment campaign:

```text
Speed      -> Isaac conveyor graph velocity
Resolution -> Isaac render-product camera width/height
CPU cores  -> Docker CPU quota on edge-dev
UL PRB %   -> FlexRIC static slice xApp
```

Each `Apply Live` writes:

```text
orbit_inspect_testbed/live_results/latest/current_live_config.json
```

The Isaac script reloads that config at the start of each queued run. The
factory relay records the same control/config fields in the live CSV. This
matches the old experiment-runner pattern at the control/config level:

```text
write config -> Isaac applies speed/resolution -> backend applies CPU/PRB -> CSV records run provenance
```

The live demo keeps the radio/core stack and Isaac session alive for
interactivity, but each `Apply Live` restarts the application relays with
experiment-runner-style per-run environment before starting the Isaac run. For
live analysis, prefer the `control_*`, `sent_resolution`, and `edge_cpus`
columns listed below.

## Files

```text
index.html                         dashboard UI
styles.css                         dashboard styling
app.js                             dashboard logic
live_backend.py                    HTTP API and live-control bridge
start_live_backend.sh              dashboard backend launcher
start_live_application_stack.sh    starts/checks edge, UE forwarder, factory relay, background traffic
start_ue_forwarder_live.sh         UE forwarder launcher
ue_forwarder_live_supervisor.sh    UE forwarder supervisor
start_factory_relay_live.sh        ORBIT-Inspect factory relay launcher
start_background_iperf_live.sh     30 Mb/s background UL launcher
reset_measured_channel_stack.sh    reset OAI/FlexRIC to measured/default RFsim config
check_live_stack.sh                full live preflight
check_live_ros.sh                  ROS control-topic visibility check
runtime_scripts/
  distance_based_spawning_orbit_inspect_live.py
  factory_relay_orbit_inspect_live.py
```

## Normal Startup Order

Run commands from the repository root:

```bash
cd /path/to/ORBIT-Inspect
```

### 1. Cache Sudo

```bash
sudo -v
```

Several live pieces need non-interactive sudo. If this expires, rerun `sudo -v`.

### 2. Reset Radio/Core To The Measured Channel

```bash
ORBIT3C_RECOVER_CORE=1 ./orbit_inspect_testbed/reset_measured_channel_stack.sh
```

This recreates the OAI/FlexRIC path using the measured/default RFsim
configuration and refuses to proceed if RFsim `chanmod` is enabled in the gNB or
UE config. The ORBIT-Inspect demo intentionally does not expose channel-model,
path-loss, or noise controls.

Expected after this step:

```text
nearRT-RIC running
OAI gNB running
OAI nrUE running
oaitun_ue1 exists
edge-dev container running
```

### 3. Start Application-Side Live Stack

```bash
./orbit_inspect_testbed/start_live_application_stack.sh
```

This checks or starts:

```text
edge relay inside edge-dev
UE forwarder on 127.0.0.1:9200
ORBIT-Inspect factory relay
30 Mb/s background UL traffic on oaitun_ue2, port 5203
```

The normal startup script starts/checks background traffic. Per-`Apply Live`
restarts from the dashboard skip background startup because that path is
non-interactive and should not require sudo.

Logs are written under:

```text
orbit_inspect_testbed/live_results/latest/logs/
```

The 30 Mb/s background value is offered traffic load, not PRB share. The PRB
knob controls the industrial slice share; the background slice receives the
remaining `100 - UL PRB %`.

The ORBIT-Inspect live demo uses the same factory/edge ROI defaults as
`codesign_prb_slice_experiment_runner_v1.py`: factory-side visibility gating is
enabled, edge-side ROI cropping is enabled, and edge ROI rotation defaults to
`cw90`. On each `Apply Live`, the backend applies the Docker CPU limit, restarts
the edge relay with `RUN_SPEED`, `EDGE_CPUS`, `EDGE_LABEL`,
`EDGE_COMPUTE_CAPACITY`, `EDGE_RESOURCE_CONTROL`, `EDGE_TORCH_THREADS`, preload,
and warm-up settings like the experiment runner, restarts the factory relay with
the per-run latency CSV, applies PRB slicing, then sends Isaac `start_run`.

Manual restarts are only needed after code changes or if a process is stuck:

```bash
ORBIT_INSPECT_RESTART_FACTORY=1 ./orbit_inspect_testbed/start_live_application_stack.sh
ORBIT_INSPECT_RESTART_EDGE=1 ./orbit_inspect_testbed/start_live_application_stack.sh
```

After changing live edge/factory code, prefer restarting both application relays
once before clicking `Apply Live`:

```bash
sudo -v
ORBIT_INSPECT_RESTART_EDGE=1 ORBIT_INSPECT_RESTART_FACTORY=1 ./orbit_inspect_testbed/start_live_application_stack.sh
```

The edge relay restart does not require `pgrep` or `pkill` inside `edge-dev`;
the launcher uses `runtime_scripts/edge_relay_process_ctl.py` to stop the old
Python process through `/proc`.

The background iperf launcher also detects whether the release is mounted inside
`edge-dev` as `/workspace` or `/workspace/ORBIT-Inspect`. If your container uses
a different mount point, set it explicitly before startup:

```bash
ORBIT_INSPECT_CONTAINER_REPO_DIR=/path/inside/edge-dev ./orbit_inspect_testbed/start_live_application_stack.sh
```

The edge output directory is shared between the host and `edge-dev`. If Docker
previously created `live_results/latest/edge_runs` as `nobody:nogroup`, the
launcher falls back to `live_results/latest/logs/edge_runs/RUNID` so both the
dashboard backend and the container can create per-run logs/CSVs without
requiring an extra ownership repair.

The edge relay binds port `9100` after model preload and warm-up. The launcher
waits up to 60 seconds by default; override this only if the model load is
slower on the demo machine:

```bash
ORBIT_INSPECT_EDGE_START_TIMEOUT_SEC=90 ./orbit_inspect_testbed/start_live_application_stack.sh
```

The factory relay is launched with a PID file at
`live_results/latest/logs/factory_relay.pid`; both the launcher and preflight
use that PID file to avoid false process checks.

### 4. Start Dashboard Backend

Use a new terminal:

```bash
cd /path/to/ORBIT-Inspect
source /opt/ros/jazzy/setup.bash
./orbit_inspect_testbed/start_live_backend.sh
```

The backend runs in real actuator mode by default. It also auto-discovers live
RNTIs when needed for FlexRIC PRB control.

Expected:

```text
[LiveBackend] Serving http://127.0.0.1:8766/orbit_inspect_testbed/index.html
```

Leave this terminal running.

### 5. Start Isaac Script

In Isaac Sim, load the factory USD and run:

```text
orbit_inspect_testbed/runtime_scripts/distance_based_spawning_orbit_inspect_live.py
```

Expected:

```text
[LiveControl] Isaac is ready. Waiting for Apply Live / start_run.
```

The script should not spawn parts until `Apply Live` sends `start_run`.

### 6. Preflight

In a ROS-sourced terminal:

```bash
source /opt/ros/jazzy/setup.bash
./orbit_inspect_testbed/check_live_stack.sh
```

Do not click `Apply Live` until preflight passes.

### 7. Open Dashboard And Run

Open:

```text
http://127.0.0.1:8766/orbit_inspect_testbed/index.html
```

Choose speed, resolution, PRB, and CPU, then click `Apply Live`.

Expected Isaac output:

```text
[LiveConfig] Loaded ...
[LiveConfig] Applying run config: ...
[CameraConfig] Resolution changed at /World/ActionGraph_01/isaac_create_render_product: ...
[LiveControl] Starting queued ORBIT-Inspect live run.
```

Expected dashboard behavior:

```text
command acknowledgments show isaac and factory_relay
live actuator status reports CPU and PRB updates
online measurement changes from waiting to collecting after CSV rows arrive
Isaac progress counts spawned/completed parts
```

## Results

Default live outputs:

```text
orbit_inspect_testbed/live_results/latest/current_live_config.json
orbit_inspect_testbed/live_results/latest/factory_latency_YYYYMMDD_HHMMSS_live_RUNID.csv
orbit_inspect_testbed/live_results/latest/factory_latency.csv -> latest Apply Live CSV
orbit_inspect_testbed/live_results/latest/edge_runs/RUNID/edge_results.csv
orbit_inspect_testbed/live_results/latest/logs/edge_runs/RUNID/edge_results.csv if edge_runs is not host-writable
orbit_inspect_testbed/live_results/latest/logs/
```

The dashboard backend creates a fresh timestamped factory CSV path on every
`Apply Live`, writes that path into `current_live_config.json`, and updates
`factory_latency.csv` to point at the latest run. The ORBIT-Inspect factory relay
switches to that path when it receives the next Isaac request/config. Restart
the factory relay only after code changes:

```bash
ORBIT_INSPECT_RESTART_FACTORY=1 ./orbit_inspect_testbed/start_live_application_stack.sh
```

For live CSV analysis, prefer:

```text
control_run_id
request_run_id
control_speed
control_resolution
control_prb
control_cpu
sent_resolution
edge_cpus
factory_pid
factory_roundtrip_ms
correct
late_for_sort_edge
```

Interpretation:

```text
control_*        selected values received from the live controller/config
request_run_id   Isaac run id carried by each capture request
sent_resolution  actual image size transmitted by the factory relay
edge_cpus        live configured Docker CPU value for the ORBIT-Inspect row
edge_reported_*  startup metadata reported by the already-running edge relay
```

For a clean run, these should match the selected knobs:

```text
control_speed      == selected speed
control_resolution == selected resolution
sent_resolution    == selected resolution
control_prb        == selected UL PRB %
control_cpu        == selected CPU cores
edge_cpus          == selected CPU cores
```

The dashboard only summarizes rows that match the current live `run_id`,
Isaac request `run_id`, speed, resolution, PRB, CPU, and actual sent
resolution.

The online COT calculation follows the old experiment analysis scripts:

```text
1. Keep only rows matching the current live run/config.
2. Remove replay, partial-visibility, no-fresh-image, and not-fully-visible rows.
3. Compute correct-on-time over the remaining valid rows.
```

A row counts as correct-on-time only if it is `correct=1` and it is not marked
late, unknown, or no-ack. Therefore COT can be 0% even while rows are arriving
if all valid rows are model misses, late for the physical sort deadline, or
missing edge acknowledgments. The dashboard reports the active CSV path plus
the correct/late/unknown/no_ack counts next to COT.

The dashboard deadline visualization uses the physical sorting deadline:

```text
deadline_ms = (sort_distance_m / conveyor_speed_mps) * 1000
```

For the ORBIT-Inspect demo, `sort_distance_m = 1.0`.
The Isaac live script sends this same deadline budget to the factory relay as
`time_to_sort_sec`. Override with `ORBIT_INSPECT_SORT_DISTANCE_M` only if the
paper/demo assumption changes.

`no_ack` and `no_result` rows are kept as failed live outcomes; they are not
removed as replay rows. The dashboard displays COT as both a percentage and
`correct_on_time_count / correct_on_time_denominator`.

The live dashboard does not remove the first valid latency row. The edge relay
preloads the model and runs warm-up inference before accepting live traffic, so
the first valid live row should be part of the online demo measurement.

## Troubleshooting

If the UE forwarder fails with `sudo: a password is required`:

```bash
cd /path/to/ORBIT-Inspect
sudo -v
./orbit_inspect_testbed/start_ue_forwarder_live.sh
```

Then rerun:

```bash
./orbit_inspect_testbed/start_live_application_stack.sh
```

If `sent_resolution` does not match `control_resolution`, restart the ORBIT-Inspect
Isaac script and confirm Isaac prints `[CameraConfig] Resolution changed ...`.
Resolution is controlled in Isaac, not by resizing inside the factory relay.

If many rows are `model_miss`, first confirm the CSV rows are using the same
ROI path as the experiment runner:

```text
roi_used_for_visibility_check == True
edge_roi_status does not start with edge_roi_disabled
edge_roi_status contains rotated_cw90
```

If `check_live_stack.sh` reports:

```text
[WARN] latest old live CSV reports edge_roi_disabled_rotated_cw90
```

that warning is about the previous completed CSV, not necessarily the currently
running edge relay. Restart the edge/factory relays with the command above, run
one new `Apply Live`, and inspect the new timestamped
`factory_latency_YYYYMMDD_HHMMSS_live_RUNID.csv`. The warning should disappear
after the latest CSV is produced by the restarted edge relay.

If the edge relay does not become reachable during startup, inspect the per-run
edge log printed by the launcher:

```text
orbit_inspect_testbed/live_results/latest/edge_runs/RUNID/edge_stdout.txt
orbit_inspect_testbed/live_results/latest/logs/edge_runs/RUNID/edge_stdout.txt if edge_runs is not host-writable
```

If the dashboard says Isaac is already running, wait for the current spawning
run to finish before clicking `Apply Live` again. Rows from an in-flight run are
not a clean measurement for the new knobs.

If ROS acknowledgments are missing:

```bash
source /opt/ros/jazzy/setup.bash
./orbit_inspect_testbed/check_live_ros.sh
```

Usually this means Isaac, backend, or factory relay were started with different
ROS environment variables.

If PRB control fails after OAI recovery, rerun the backend launcher so it can
rediscover current RNTIs:

```bash
./orbit_inspect_testbed/start_live_backend.sh
```

Do not reuse old RNTIs after OAI recovery or UE reconnect.

## Measured Replay Only

For viewing the measured 1056-point design space without live control:

```bash
cd /path/to/ORBIT-Inspect
python -m http.server 8765
```

Open:

```text
http://localhost:8765/orbit_inspect_testbed/index.html
```

This mode reads:

```text
automated_experiments/plots_grid_1056_analysis_cleaned/summary_current_grid.csv
```

Measured replay is the source for paper design-space claims. Live mode is the
interactive demonstration and can run values outside the measured grid.
