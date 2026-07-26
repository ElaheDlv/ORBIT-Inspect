#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${ORBIT_INSPECT_PORT:-8766}"
RESULTS_DIR="${ORBIT_INSPECT_RESULTS_DIR:-$PWD/orbit_inspect_testbed/live_results/latest}"
LIVE_RNTI_FILE="${ORBIT3C_LIVE_RNTI_FILE:-$RESULTS_DIR/live_rntis.json}"
PYTHON_BIN="${ORBIT_INSPECT_PYTHON:-${ORBIT3C_ROS_PYTHON:-/usr/bin/python3}}"

mkdir -p "${RESULTS_DIR}"

export ORBIT_LIVE_LATENCY_CSV="${ORBIT_LIVE_LATENCY_CSV:-$RESULTS_DIR/factory_latency.csv}"
export ORBIT_LIVE_APPLY="${ORBIT_LIVE_APPLY:-1}"

PORT_STATUS="$(python3 - "$PORT" "$PWD" <<'PY'
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

port = int(sys.argv[1])
repo = str(Path(sys.argv[2]).resolve())
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
except urllib.error.URLError:
    raise SystemExit(0)
except Exception as exc:
    print(f"PORT_IN_USE_UNKNOWN {exc}")
    raise SystemExit(0)

state = payload.get("state", {})
path = state.get("latency_csv_path") or state.get("online_measurement", {}).get("path") or ""
if path and str(Path(path).resolve()).startswith(repo + "/"):
    print("SAME_REPO")
else:
    print(f"OTHER_REPO {path or 'unknown_results_path'}")
PY
)"

if [ "$PORT_STATUS" = "SAME_REPO" ]; then
  echo "[ORBIT-Inspect] Backend is already running for this repo on port $PORT."
  echo "[ORBIT-Inspect] Open http://127.0.0.1:$PORT/orbit_inspect_testbed/index.html"
  exit 0
elif printf '%s' "$PORT_STATUS" | grep -q '^OTHER_REPO '; then
  echo "[ORBIT-Inspect ERROR] Port $PORT is already serving an ORBIT-Inspect backend from another repo."
  echo "[ORBIT-Inspect ERROR] Active backend result path: ${PORT_STATUS#OTHER_REPO }"
  echo "[ORBIT-Inspect ERROR] Stop the old backend/factory relays before starting this release repo:"
  echo "  pkill -f 'orbit_inspect_testbed/live_backend.py'"
  echo "  pkill -f 'factory_relay_orbit_inspect_live.py'"
  exit 1
elif printf '%s' "$PORT_STATUS" | grep -q '^PORT_IN_USE_UNKNOWN '; then
  echo "[ORBIT-Inspect ERROR] Port $PORT is in use but does not look like this ORBIT-Inspect backend."
  echo "[ORBIT-Inspect ERROR] Detail: ${PORT_STATUS#PORT_IN_USE_UNKNOWN }"
  exit 1
fi

if [ "$ORBIT_LIVE_APPLY" = "1" ] && [ -z "${ORBIT3C_SLICE_RNTI:-}" ] && [ -z "${ORBIT3C_BG_RNTI:-}" ] && [ ! -s "$LIVE_RNTI_FILE" ]; then
  if [ "${ORBIT3C_AUTO_DISCOVER_RNTI:-1}" = "1" ]; then
    echo "[ORBIT-Inspect] No live RNTI mapping found; probing oaitun_ue1/oaitun_ue2 automatically."
    /usr/bin/python3 orbit_inspect_testbed/discover_live_rntis.py --output "$LIVE_RNTI_FILE"
  else
    echo "[ORBIT-Inspect ERROR] Real PRB control needs live RNTIs. Run discover_live_rntis.py or set ORBIT3C_AUTO_DISCOVER_RNTI=1."
    exit 1
  fi
fi

"$PYTHON_BIN" orbit_inspect_testbed/live_backend.py --port "${PORT}"
