# Presenting ORBIT-Inspect

[Repository overview](../README.md) ·
[Operator guide](../orbit_inspect_testbed/README.md) ·
[Full setup](../orbit_inspect_testbed/SETUP_0_TO_100.md)

## Choose the demonstration mode

Use **measured replay** to present the design space without the external testbed.
Use **live mode** when the Isaac Sim, radio, core, edge, and relay stack is ready
and you want to demonstrate a new inspection run.

For replay, run this command from the repository root:

```bash
python3 -m http.server 8765 --bind 127.0.0.1
```

Open <http://127.0.0.1:8765/orbit_inspect_testbed/index.html>. Confirm that the
controller mode reads **Measured replay** and the page shows **960 measured
points** for the current bundled summary. **Fallback** means the CSV did not load;
resolve that before presenting any displayed values as measured results.

For live mode, follow the [startup sequence](../orbit_inspect_testbed/README.md#normal-startup-order)
and run `./orbit_inspect_testbed/check_live_stack.sh` before the presentation.

## A five-minute walkthrough

| Time | Action | Point to explain |
| --- | --- | --- |
| 0:00–0:45 | Introduce the factory inspection loop | A correct decision must return before the part reaches the sorting point. |
| 0:45–1:30 | Select **Nominal** and show the current operating point | Explain conveyor speed, resolution, uplink PRB allocation, and edge CPU allocation. |
| 1:30–2:30 | Select **Stress**, then **Scarce** | Read the observed metric changes. Explain the shared timing budget. |
| 2:30–3:30 | Inspect the feasibility map, then click **Recover** | Explain the recommendation drawn from measured configurations. In replay, Recover changes the selected operating point. |
| 3:30–4:15 | Click **Play** under Adaptation Replay | Show recommendations changing as replay advances through conveyor speeds. |
| 4:15–5:00 | Open the demo video and paper | Connect the interface to the recorded demonstration and research methodology. |

Let the displayed measurements guide the explanation. Do not promise a particular
improvement before checking the selected configuration.

## Optional live run

1. Open <http://127.0.0.1:8766/orbit_inspect_testbed/index.html> with the configured backend running.
2. Enter the intended settings in the **Live testbed** fields and click **Apply Live**.
3. Show **Command acknowledgments**, **Isaac progress**, and **Observed online config** to establish what actually ran.
4. Wait for completion, then show **Online measurement** and **Result CSV**.
5. Retain the run CSV with your experiment records if it will support a research claim.

The replay selection and live input fields are distinct. Confirm the live fields
before applying a run. Live settings can fall outside the measured grid; identify
the resulting values as new measurements.

## Before the audience arrives

- Open the README, dashboard, [video](https://youtu.be/S_SA_UToFLU), and [paper](https://arxiv.org/pdf/2608.13372).
- Check screen sharing and browser zoom so the controls and metrics are readable.
- For live mode, check the stack and confirm the active configuration before starting.
- Keep measured replay and the recorded video available if the live stack becomes unavailable.

## What each metric communicates

| Metric | Interpretation |
| --- | --- |
| Correct-on-time rate | Fraction of inspections that are both correct and returned before the process deadline |
| Production goodput | Correct, on-time parts per minute |
| p95 roundtrip latency | The 95th percentile of end-to-end roundtrip delay |
| Resource cost | The dashboard's resource-allocation measure; consult the paper for the production-efficiency definition |

Measured replay explores a saved experiment summary. A live demonstration produces
new run data. Neither mode alone replaces the paper's experimental methodology.
