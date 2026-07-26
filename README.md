# ORBIT-Inspect Live Demo

This repository contains the ORBIT-Inspect interactive live-demo artifact for
the ORBIT-Inspect testbed. It packages the dashboard, live backend, Isaac runtime script,
factory/UE/edge relays, measured replay summary, and recovery/preflight scripts.

The artifact is not a replacement for Isaac Sim, OpenAirInterface, FlexRIC, or
the OAI core. Those systems must be installed separately and configured as
described in:

```text
orbit_inspect_testbed/SETUP_0_TO_100.md
```

## Quick Start On The Configured Demo Machine

From the repository root:

```bash
cd /path/to/ORBIT-Inspect
source /opt/ros/jazzy/setup.bash
sudo -v
ORBIT3C_RECOVER_CORE=1 ./orbit_inspect_testbed/reset_measured_channel_stack.sh
./orbit_inspect_testbed/start_live_application_stack.sh
./orbit_inspect_testbed/start_live_backend.sh
```

In Isaac Sim, run:

```text
orbit_inspect_testbed/runtime_scripts/distance_based_spawning_orbit_inspect_live.py
```

Then verify:

```bash
source /opt/ros/jazzy/setup.bash
./orbit_inspect_testbed/check_live_stack.sh
```

Open:

```text
http://127.0.0.1:8766/orbit_inspect_testbed/index.html
```

## Included Runtime Files

```text
orbit_inspect_testbed/           dashboard, backend, launchers, Isaac scripts
edge_relay_oai_dev.py                  edge-side YOLO TCP service
ue_forwarder_oai_dev.py                UE tunnel forwarder
best_n.pt                              YOLO model used by the demo
automated_experiments/scripts/         OAI recovery helper used by ORBIT-Inspect
automated_experiments/plots_grid_1056_analysis_cleaned/summary_current_grid.csv
```

Live outputs are written under:

```text
orbit_inspect_testbed/live_results/latest/
```

The directory is present in the repo through `.gitkeep` files, but generated
CSV/log outputs are intentionally ignored by git. After each `Apply Live`, look
for:

```text
orbit_inspect_testbed/live_results/latest/factory_latency_YYYYMMDD_HHMMSS_live_RUNID.csv
orbit_inspect_testbed/live_results/latest/factory_latency.csv
```

The dashboard also shows the active result CSV path under `Result CSV`.
