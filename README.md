<p align="center">
  <img src="docs/assets/orbit-inspect-banner.svg" alt="ORBIT-Inspect: sensing, 5G O-RAN, and edge intelligence for process-aware industrial inspection" width="100%">
</p>

<p align="center">
  <strong>An interactive research testbed for process-aware industrial inspection.</strong><br>
  Explore how sensing, wireless resources, and edge computing affect correct, on-time decisions.
</p>

<p align="center">
  <a href="https://youtu.be/S_SA_UToFLU">Watch the demo</a> ·
  <a href="https://arxiv.org/abs/2608.13372">Read the paper</a> ·
  <a href="#try-the-dashboard">Try the dashboard</a> ·
  <a href="docs/DEMO.md">Presentation guide</a> ·
  <a href="orbit_inspect_testbed/SETUP_0_TO_100.md">Full setup</a>
</p>

## Why ORBIT-Inspect?

A correct inspection result only helps if it arrives before the part reaches the
sorting point. Increasing conveyor speed shortens that window; increasing camera
resolution changes both the wireless payload and the inference workload.
ORBIT-Inspect makes these dependencies visible through an Isaac Sim digital twin,
programmable 5G O-RAN, and edge visual inference.

| Explore | Observe |
| --- | --- |
| Conveyor speed and camera resolution | Changes in process deadlines and sensing workloads |
| Uplink physical resource block (PRB) allocation and edge CPU quota | Effects of communication and compute resources on the inspection loop |
| Nominal, Stress, and Scarce scenarios | Correct-on-time rate, production goodput, and latency |
| Controller recommendations and adaptation replay | Operating points selected from the measured design space |

## How the inspection loop works

![Factory inspection loop: sense, communicate over 5G, compute at the edge, and return a decision before the process deadline. The same example latency misses the faster-process deadline but meets the slower-process deadline.](docs/assets/inspection-loop.png)

Conveyor speed determines the time available for inspection. Sensing,
communication, and edge inference must deliver a correct decision before the part
reaches the sorting point. The example holds end-to-end latency constant to
illustrate how a faster process can turn an on-time decision into a late one;
actual latency varies with the operating configuration. Timing ends at decision
return, before physical sorting is completed.

## Try the dashboard

**Measured replay** is the quickest way to explore the interface. It uses the
bundled experiment summary and requires Python 3 and a browser.

From the repository root:

```bash
python3 -m http.server 8765 --bind 127.0.0.1
```

Open **[the replay dashboard](http://127.0.0.1:8765/orbit_inspect_testbed/index.html)**.
Select **Nominal**, **Stress**, or **Scarce**, explore the feasibility map, then
click **Play** under Adaptation Replay.

The controller mode should read **Measured replay**. If it reads **Fallback**, the
measured CSV failed to load and the displayed values come from a fallback model;
do not present those values as experimental results. Live-control functions require
the backend and configured external testbed.

| Mode | Behavior | Requirements |
| --- | --- | --- |
| Measured replay | Explores bundled measured configurations without actuating the testbed | Python 3, browser |
| Live testbed | Applies settings to the running stack and records a new inspection run | Isaac Sim, ROS 2 Jazzy, OAI, FlexRIC, Docker, configured relays |

## Run the live testbed

These commands assume the external stack is installed and configured. For
installation and machine-specific configuration, follow the
[complete setup guide](orbit_inspect_testbed/SETUP_0_TO_100.md).

In a terminal at the repository root:

```bash
source /opt/ros/jazzy/setup.bash
sudo -v
ORBIT3C_RECOVER_CORE=1 ./orbit_inspect_testbed/reset_measured_channel_stack.sh
./orbit_inspect_testbed/start_live_application_stack.sh
./orbit_inspect_testbed/start_live_backend.sh
```

In Isaac Sim, run
[`distance_based_spawning_orbit_inspect_live.py`](orbit_inspect_testbed/runtime_scripts/distance_based_spawning_orbit_inspect_live.py).
Then verify the stack in a second terminal:

```bash
source /opt/ros/jazzy/setup.bash
./orbit_inspect_testbed/check_live_stack.sh
```

Open **[the live dashboard](http://127.0.0.1:8766/orbit_inspect_testbed/index.html)**.
See the [operator guide](orbit_inspect_testbed/README.md) for run acknowledgments,
measurement fields, and recovery instructions, or use the
[presentation guide](docs/DEMO.md) for a short walkthrough.

## Repository map

| Path | Purpose |
| --- | --- |
| [`orbit_inspect_testbed/`](orbit_inspect_testbed/) | Dashboard, backend, launchers, preflight checks, and runtime scripts |
| [`orbit_inspect_testbed/SETUP_0_TO_100.md`](orbit_inspect_testbed/SETUP_0_TO_100.md) | External dependencies and full testbed setup |
| [`docs/DEMO.md`](docs/DEMO.md) | Demo preparation and presentation sequence |
| [`automated_experiments/`](automated_experiments/) | Recovery helper and measured replay summary |
| [`edge_relay_oai_dev.py`](edge_relay_oai_dev.py) | Edge-side YOLO TCP service |
| [`ue_forwarder_oai_dev.py`](ue_forwarder_oai_dev.py) | UE tunnel forwarder |
| [`best_n.pt`](best_n.pt) | Model weights used by the demo |
| [`isaac_sim.usd`](isaac_sim.usd) | Bundled Isaac scene asset |

## Measurements and reproducibility

Replay reads
[`summary_current_grid.csv`](automated_experiments/plots_grid_1056_analysis_cleaned/summary_current_grid.csv).
The bundled summary currently contains **960 configuration rows**. The directory
name is retained from the experiment workflow; it is not the row count.

Live runs can use settings outside this measured grid. Treat their outputs as new
measurements rather than interchangeable reproductions of the paper results.
The dashboard reports correct-on-time rate, production goodput, p95 roundtrip
latency, and resource cost.

Generated live CSV and log files are ignored by Git and written under
`orbit_inspect_testbed/live_results/latest/`. After **Apply Live**, the dashboard's
**Result CSV** field identifies the output. Typical files include:

```text
factory_latency_YYYYMMDD_HHMMSS_live_RUNID.csv
factory_latency.csv
```

## Demo video

<p align="center">
  <a href="https://youtu.be/S_SA_UToFLU">
    <img src="https://img.youtube.com/vi/S_SA_UToFLU/hqdefault.jpg" alt="Watch the ORBIT-Inspect demonstration on YouTube" width="640">
  </a>
</p>

<p align="center"><a href="https://youtu.be/S_SA_UToFLU"><strong>▶ Watch the ORBIT-Inspect demo</strong></a></p>

## Research paper

**Process-Aware Cross-Layer Adaptation for O-RAN-Enabled Industrial Systems**

Elahe Delavari, Junaid Farooq, M. Majid Butt, and Quanyan Zhu.

The paper presents PACE, a framework for coordinating sensing, radio, and edge
computing around physical process deadlines, evaluated through a factory-inspection
case study.

[Read on arXiv](https://arxiv.org/abs/2608.13372) ·
[Download PDF](https://arxiv.org/pdf/2608.13372)

<details>
<summary>Cite this work (BibTeX)</summary>

```bibtex
@misc{delavari2026processaware,
  title         = {Process-Aware Cross-Layer Adaptation for O-RAN-Enabled Industrial Systems},
  author        = {Elahe Delavari and Junaid Farooq and M. Majid Butt and Quanyan Zhu},
  year          = {2026},
  eprint        = {2608.13372},
  archivePrefix = {arXiv},
  primaryClass  = {eess.SY},
  url           = {https://arxiv.org/abs/2608.13372}
}
```

</details>
