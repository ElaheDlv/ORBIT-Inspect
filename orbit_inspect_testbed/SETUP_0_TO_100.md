# ORBIT-Inspect Demo: 0-to-100 Setup Guide

This guide describes the complete setup needed to run the ORBIT-Inspect interactive
live demo. It is written for an ORBIT-Inspect-only release: the reader should be able
to understand what external systems must be installed, what files this demo
expects, and what commands bring the live testbed up.

The live demo assumes the same OAI/FlexRIC/Isaac path used by the measured
experiment runner, but it exposes the knobs interactively through the dashboard.

## 1. System Architecture

```text
Isaac Sim factory
  publishes images/metadata on /fortuna_inspection/request
        |
        v
ORBIT-Inspect factory relay on host
  crops/checks factory ROI, sends image to local UE forwarder
        |
        v
UE forwarder on host, bound to oaitun_ue1
  sends TCP payload through OAI UE tunnel
        |
        v
OAI nrUE -> RFsim -> OAI gNB -> OAI core / UPF
        |
        v
edge-dev Docker container at 192.168.83.150
  runs YOLO edge relay on TCP 9100
        |
        v
result returns to factory relay, dashboard records online CSV metrics
```

A second UE tunnel, `oaitun_ue2`, is used for 30 Mb/s background UL traffic.
FlexRIC controls the UL PRB split between the industrial UE and the background
UE.

## 2. Required Host Software

Install these before using the ORBIT-Inspect folder:

```text
Ubuntu host with sudo access
Docker and docker compose
Python 3 for host utilities
ROS 2 Jazzy, including rclpy for /usr/bin/python3
Isaac Sim with the factory USD scene
OpenAirInterface 5G RAN with RFsim and E2/FlexRIC support
OAI 5G core / UPF Docker setup
FlexRIC nearRT-RIC and static slice xApp
iperf3 on the host and inside edge-dev
```

Important: ROS Jazzy Python should be `/usr/bin/python3`. Conda Python usually
cannot import the ROS Jazzy `rclpy` binary extension.

## 3. Required Repository Files

The standalone ORBIT-Inspect repo should contain at least:

```text
orbit_inspect_testbed/
  README.md
  SETUP_0_TO_100.md
  index.html
  styles.css
  app.js
  live_backend.py
  check_live_stack.sh
  reset_measured_channel_stack.sh
  recover_live_radio_stack.sh
  start_live_application_stack.sh
  start_live_backend.sh
  start_factory_relay_live.sh
  start_ue_forwarder_live.sh
  start_background_iperf_live.sh
  discover_live_rntis.py
  set_live_rntis.py
  probe_live_ros.py
  check_live_ros.sh
  ue_forwarder_live_supervisor.sh
  runtime_scripts/
    distance_based_spawning_orbit_inspect_live.py
    factory_relay_orbit_inspect_live.py
    edge_relay_process_ctl.py

edge_relay_oai_dev.py
ue_forwarder_oai_dev.py
best_n.pt
automated_experiments/scripts/recover_oai_stack.sh
automated_experiments/plots_grid_1056_analysis_cleaned/summary_current_grid.csv
```

For a clean public repo, large files such as `best_n.pt` and Isaac USD/assets
can be distributed separately, but the README must state the exact expected
paths and checksums.

## 4. Expected Paths And Network Values

Set these shell variables before following the commands below. The defaults
shown are example paths; change them for your installation:

```bash
export CO_DESIGN_DIR=/path/to/ORBIT-Inspect
export ORBIT3C_DEV_DIR=/home/elahe/user/ORBIT3C_OAI_DEV
export ORBIT3C_OAI_RAN_DIR="${ORBIT3C_DEV_DIR}/oai_ran/openairinterface5g"
export ORBIT3C_MEASURED_CONFIG_DIR="${ORBIT3C_DEV_DIR}/configs"
```

The scripts default to these paths and IPs:

```text
Code root:
  /path/to/ORBIT-Inspect

OAI dev root:
  /home/elahe/user/ORBIT3C_OAI_DEV

OAI/FlexRIC source:
  /home/elahe/user/ORBIT3C_OAI_DEV/oai_ran/openairinterface5g

Measured/default RFsim config:
  /home/elahe/user/ORBIT3C_OAI_DEV/configs

gNB config:
  gnb_orbit3c_dev_rfsim_e2.conf

2-UE config:
  ue_orbit3c_dev_rfsim_2ue.conf

edge-dev IP:
  192.168.83.150

edge relay port:
  9100

UE forwarder port:
  9200

dashboard backend:
  http://127.0.0.1:8766/orbit_inspect_testbed/index.html
```

Override these with environment variables if your release uses different paths:

```text
ORBIT3C_MEASURED_CONFIG_DIR
ORBIT3C_EDGE_CONTAINER_NAME
ORBIT3C_EDGE_IP
ORBIT3C_EDGE_PORT
ORBIT3C_UE_FORWARDER_PORT
ORBIT3C_SLICE_XAPP
ORBIT_INSPECT_RESULTS_DIR
ORBIT_INSPECT_PORT
```

## 5. One-Time OAI Dev Core Setup

Create or reuse an OAI dev core that is separate from any stable UERANSIM core.
The expected dev network plan is:

```text
orbit3c-dev-public-net  -> 192.168.80.0/24
orbit3c-dev-access      -> 192.168.82.0/24
orbit3c-dev-core        -> 192.168.83.0/24

oai-amf-dev             -> 192.168.80.132
oai-smf-dev             -> 192.168.80.133
vpp-upf-dev N3          -> 192.168.82.201
vpp-upf-dev N6          -> 192.168.83.201
edge-dev                -> 192.168.83.150
```

The dev slice/DNN is:

```text
DNN = oai
SST = 1
SD  = FFFFFF
```

The historical full OAI setup details live in the old root guide
`OAI_DEV_FULL_SETUP_GUIDE.md`. For a standalone ORBIT-Inspect repo, copy the stable
parts of that guide into an `oai_setup/` or `docs/oai_dev_core.md` document.

## 6. Create The edge-dev Container

If `edge-dev` does not exist, create it on the OAI dev core network:

```bash
docker run -dit \
  --name edge-dev \
  --cap-add NET_ADMIN \
  --network orbit3c-dev-core \
  --ip 192.168.83.150 \
  -v "${CO_DESIGN_DIR}:/workspace" \
  python:3.12-slim \
  bash
```

Install runtime dependencies inside the container:

```bash
docker exec -it edge-dev bash -lc \
  "apt-get update && apt-get install -y iproute2 iputils-ping iperf3 libxcb1 libx11-6 libglib2.0-0 libgl1"

docker exec -it edge-dev bash -lc \
  "pip install ultralytics opencv-python-headless pandas pyyaml"
```

Add the return route to the OAI UE subnet:

```bash
docker exec -it edge-dev ip route add 12.1.1.128/25 via 192.168.83.201 dev eth0
```

Verify:

```bash
docker exec -it edge-dev bash -lc "ls -lh /workspace/edge_relay_oai_dev.py /workspace/best_n.pt"
docker exec -it edge-dev ip route
```

## 7. Build/Verify FlexRIC And Slice xApp

The ORBIT-Inspect backend expects the static slice xApp here by default:

```text
/home/elahe/user/ORBIT3C_OAI_DEV/oai_ran/openairinterface5g/openair2/E2AP/flexric/build/examples/xApp/c/slice/xapp_orbit3c_static_slice_ctrl
```

Verify:

```bash
test -x "${ORBIT3C_OAI_RAN_DIR}/openair2/E2AP/flexric/build/examples/xApp/c/slice/xapp_orbit3c_static_slice_ctrl"
```

Also verify nearRT-RIC can run:

```bash
cd "${ORBIT3C_OAI_RAN_DIR}/openair2/E2AP/flexric"
./build/examples/ric/nearRT-RIC
```

Stop it after checking; the recovery script starts it during normal setup.

## 8. Prepare Measured/Default RFsim Config

ORBIT-Inspect live mode intentionally uses the measured/default RFsim path. It should
not use RFsim `chanmod`, path loss, or AWGN controls.

Expected config files:

```text
/home/elahe/user/ORBIT3C_OAI_DEV/configs/gnb_orbit3c_dev_rfsim_e2.conf
/home/elahe/user/ORBIT3C_OAI_DEV/configs/ue_orbit3c_dev_rfsim_2ue.conf
```

The preflight fails if either config enables `chanmod`.

## 9. Start From A Clean Shell

Use the repository root:

```bash
cd "${CO_DESIGN_DIR}"
source /opt/ros/jazzy/setup.bash
sudo -v
sudo -n true && echo "sudo cache works"
```

Clear stale manual channel/RNTI overrides before a clean demo:

```bash
unset ORBIT3C_SLICE_RNTI
unset ORBIT3C_BG_RNTI
unset ORBIT3C_CHANNEL_MODEL
unset ORBIT3C_CHANNEL_PROFILE
unset ORBIT3C_CHANNEL_NOISE_POWER_DB
unset ORBIT3C_CHANNEL_PLOSS_DB
unset ORBIT3C_GNB_CMD
unset ORBIT3C_UE_CMD
```

## 10. Start/Recover OAI, FlexRIC, gNB, And 2 UEs

Run:

```bash
ORBIT3C_RECOVER_CORE=1 ./orbit_inspect_testbed/reset_measured_channel_stack.sh
```

This script:

```text
checks the measured/default RFsim configs
rejects configs with chanmod enabled
sets ORBIT3C_CONFIG_DIR
uses nohup-based recovery to avoid tmux sudo prompts
delegates to automated_experiments/scripts/recover_oai_stack.sh
```

Expected after recovery:

```text
nearRT-RIC process is running
OAI gNB process is running
OAI nrUE process is running
oaitun_ue1 exists
oaitun_ue2 exists
edge-dev container is running
```

## 11. Start The ORBIT-Inspect Application Stack

Run:

```bash
./orbit_inspect_testbed/start_live_application_stack.sh
```

This starts or verifies:

```text
edge relay in edge-dev, TCP 192.168.83.150:9100
UE forwarder on host, TCP 127.0.0.1:9200
ORBIT-Inspect factory relay with ROS Jazzy Python
30 Mb/s background iperf on oaitun_ue2
```

After code changes, restart the app relays:

```bash
ORBIT_INSPECT_RESTART_EDGE=1 ORBIT_INSPECT_RESTART_FACTORY=1 \
  ./orbit_inspect_testbed/start_live_application_stack.sh
```

The dashboard backend uses the same script during `Apply Live`, but skips
background iperf startup so that interactive runs do not require sudo.

## 12. Start The Dashboard Backend

Use a separate terminal:

```bash
cd "${CO_DESIGN_DIR}"
source /opt/ros/jazzy/setup.bash
./orbit_inspect_testbed/start_live_backend.sh
```

Expected:

```text
[LiveBackend] Serving http://127.0.0.1:8766/orbit_inspect_testbed/index.html
```

The backend runs in real actuator mode by default:

```text
ORBIT_LIVE_APPLY=1
```

It auto-discovers RNTIs if `live_results/latest/live_rntis.json` is missing.
RNTIs can change after OAI recovery, so do not reuse old RNTIs across recovery
unless preflight confirms they are still correct.

## 13. Start Isaac Sim

Open Isaac Sim, load the factory USD scene, and run:

```text
orbit_inspect_testbed/runtime_scripts/distance_based_spawning_orbit_inspect_live.py
```

Expected Isaac output:

```text
[LiveControl] Isaac is ready. Waiting for Apply Live / start_run.
```

The ORBIT-Inspect script should not spawn parts until the dashboard sends
`start_run`.

## 14. Run Preflight

In a ROS-sourced terminal:

```bash
cd "${CO_DESIGN_DIR}"
source /opt/ros/jazzy/setup.bash
./orbit_inspect_testbed/check_live_stack.sh
```

Expected for a ready demo:

```text
[OK]   gNB config has RFsim chanmod disabled
[OK]   UE config has RFsim chanmod disabled
[OK]   Docker container edge-dev is running
[OK]   Docker CPU control can inspect edge-dev
[OK]   FlexRIC slice xApp exists
[OK]   Live PRB RNTIs are configured ...
[OK]   oaitun_ue1 exists
[OK]   oaitun_ue2 exists
[OK]   30 Mb/s background iperf process is running ...
[OK]   edge relay TCP endpoint is reachable at 192.168.83.150:9100
[OK]   UE forwarder TCP endpoint is reachable at 127.0.0.1:9200
[OK]   edge relay uses experiment-runner edge ROI default
[OK]   edge relay uses experiment-runner ROI rotation default
[OK]   nearRT-RIC process is running
[OK]   OAI gNB process is running
[OK]   OAI nrUE process is running
[OK]   ORBIT-Inspect factory relay process is running
[OK]   Isaac acknowledged /orbit_inspect/control probe
[OK]   factory relay acknowledged /orbit_inspect/control probe
[OK]   factory relay is subscribed to /fortuna_inspection/request
```

Do not run the live demo until preflight passes. A warning about an old CSV
with `edge_roi_disabled_rotated_cw90` is acceptable only after the running edge
process checks pass; it disappears after a new good `Apply Live` run.

## 15. Open Dashboard And Run

Open:

```text
http://127.0.0.1:8766/orbit_inspect_testbed/index.html
```

Choose:

```text
conveyor speed
camera resolution
UL PRB allocation
edge CPU allocation
```

Click `Apply Live`.

Expected behavior:

```text
Isaac starts one queued spawning run.
Dashboard shows Isaac progress.
Command acknowledgments include isaac and factory_relay.
Live actuator status reports Docker CPU and FlexRIC PRB results.
Online measurement changes from waiting to collecting/finished.
A fresh factory_latency_YYYYMMDD_HHMMSS_live_RUNID.csv is created.
```

## 16. Result Files

Default live result directory:

```text
orbit_inspect_testbed/live_results/latest/
```

Important outputs:

```text
current_live_config.json
live_state.json
live_rntis.json
factory_latency_YYYYMMDD_HHMMSS_live_RUNID.csv
factory_latency.csv -> latest factory CSV symlink
edge_runs/RUNID/edge_results.csv
logs/edge_runs/RUNID/edge_results.csv if edge_runs is not host-writable
logs/factory_stdout.txt
logs/ue_forwarder_stdout.txt
logs/slice_xapp_stdout.txt
logs/background_iperf_*.txt/json
```

## 17. Deadline Assumption

The dashboard visualizes the physical sorting deadline as:

```text
deadline_ms = (sort_distance_m / conveyor_speed_mps) * 1000
```

For this ORBIT-Inspect demo:

```text
sort_distance_m = 1.0
```

Correct-on-time is still computed from logged rows using the experiment-runner
analysis convention. The dashboard separates:

```text
p95 late  -> p95 roundtrip exceeds the physical sorting deadline
low COT   -> latency may be before the deadline, but correctness/COT is low
```

## 18. What Not To Include In An ORBIT-Inspect-Only Repo

Do not publish the whole historical research workspace as the ORBIT-Inspect demo. It
contains old experiments, notebooks, partial scripts, and generated outputs that
make reproducibility harder.

Publish only:

```text
ORBIT-Inspect live dashboard and runtime scripts
minimal OAI/FlexRIC setup docs/scripts
edge relay and UE forwarder used by the live path
measured replay summary CSV
model/asset instructions or checksums
preflight and troubleshooting scripts
```

Keep the old experiment runner documented as the validation baseline, but do
not make users run the full sweep just to use the live demo.

## 19. Common Failures

### Docker permission denied

Run from a user that can access Docker, or add the user to the Docker group.

### sudo password requested repeatedly

Run:

```bash
sudo -v
```

The ORBIT-Inspect recovery path uses `nohup` by default to avoid tmux panes asking
for sudo separately.

### UE forwarder missing

Run:

```bash
./orbit_inspect_testbed/start_ue_forwarder_live.sh
```

### Edge relay not reachable

Restart the app stack:

```bash
ORBIT_INSPECT_RESTART_EDGE=1 ./orbit_inspect_testbed/start_live_application_stack.sh
```

Then inspect the printed edge log path.

### PRB control fails

Rediscover RNTIs:

```bash
/usr/bin/python3 orbit_inspect_testbed/discover_live_rntis.py \
  --output orbit_inspect_testbed/live_results/latest/live_rntis.json
```

Restart the dashboard backend afterward.

### Isaac does not start on Apply Live

Check ROS visibility:

```bash
source /opt/ros/jazzy/setup.bash
./orbit_inspect_testbed/check_live_ros.sh
```

Isaac must be running the ORBIT-Inspect live script and waiting for `Apply Live`.
