#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

EDGE_CONTAINER="${ORBIT3C_EDGE_CONTAINER_NAME:-edge-dev}"
EDGE_IP="${ORBIT3C_EDGE_IP:-192.168.83.150}"
BG_IFACE="${ORBIT3C_BG_TUN_IFACE:-oaitun_ue2}"
BG_RATE_MBIT="${ORBIT3C_BG_IPERF_MBIT:-30}"
BG_DIRECTION="${ORBIT3C_BG_IPERF_DIRECTION:-ul}"
BG_PORT="${ORBIT3C_BG_IPERF_PORT:-5203}"
BG_DURATION_SEC="${ORBIT3C_BG_IPERF_SEC:-86400}"
RESULTS_DIR="${ORBIT_INSPECT_RESULTS_DIR:-$PWD/orbit_inspect_testbed/live_results/latest}"
LOG_DIR="$RESULTS_DIR/logs"
CONTAINER_REPO_DIR="${ORBIT_INSPECT_CONTAINER_REPO_DIR:-}"

mkdir -p "$LOG_DIR"

if [ -z "$BG_RATE_MBIT" ] || [ "$BG_RATE_MBIT" = "0" ]; then
  echo "[ORBIT-Inspect] Background iperf disabled by ORBIT3C_BG_IPERF_MBIT=$BG_RATE_MBIT"
  exit 0
fi

if [ "$BG_DIRECTION" != "ul" ] && [ "$BG_DIRECTION" != "dl" ]; then
  echo "[ORBIT-Inspect ERROR] ORBIT3C_BG_IPERF_DIRECTION must be ul or dl."
  exit 1
fi

if ! ip link show "$BG_IFACE" >/dev/null 2>&1; then
  echo "[ORBIT-Inspect ERROR] Background interface $BG_IFACE is missing."
  echo "Start/recover the two-UE OAI stack before starting background traffic."
  exit 1
fi

BG_BIND_IP="$(ip -4 -o addr show dev "$BG_IFACE" | awk '{print $4}' | cut -d/ -f1 | head -n 1)"
if [ -z "$BG_BIND_IP" ]; then
  echo "[ORBIT-Inspect ERROR] Could not find IPv4 address for $BG_IFACE."
  exit 1
fi

if ! sudo -n true; then
  echo "[ORBIT-Inspect ERROR] sudo cache is not available."
  echo "Run sudo -v first, then rerun this script."
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$EDGE_CONTAINER"; then
  echo "[ORBIT-Inspect ERROR] Docker container $EDGE_CONTAINER is not running."
  exit 1
fi

if [ -z "$CONTAINER_REPO_DIR" ]; then
  REPO_BASENAME="$(basename "$PWD")"
  if docker exec "$EDGE_CONTAINER" test -f "/workspace/$REPO_BASENAME/edge_relay_oai_dev.py" >/dev/null 2>&1; then
    CONTAINER_REPO_DIR="/workspace/$REPO_BASENAME"
  elif docker exec "$EDGE_CONTAINER" test -f "/workspace/edge_relay_oai_dev.py" >/dev/null 2>&1; then
    CONTAINER_REPO_DIR="/workspace"
  else
    echo "[ORBIT-Inspect ERROR] Could not find ORBIT-Inspect repo inside $EDGE_CONTAINER."
    echo "Expected either /workspace/$REPO_BASENAME or /workspace to contain edge_relay_oai_dev.py."
    echo "If your mount is different, set ORBIT_INSPECT_CONTAINER_REPO_DIR=/path/in/container."
    exit 1
  fi
fi

CONTAINER_LOG_DIR="$CONTAINER_REPO_DIR/orbit_inspect_testbed/live_results/latest/logs"
SERVER_LOG="$CONTAINER_LOG_DIR/background_iperf_server_stdout.txt"

if ! docker exec "$EDGE_CONTAINER" sh -lc "command -v iperf3 >/dev/null 2>&1"; then
  echo "[ORBIT-Inspect ERROR] iperf3 is not available inside $EDGE_CONTAINER."
  exit 1
fi

if ! command -v iperf3 >/dev/null 2>&1; then
  echo "[ORBIT-Inspect ERROR] host iperf3 is not available."
  exit 1
fi

if pgrep -f "iperf3.*-c $EDGE_IP.*-p $BG_PORT.*-B $BG_BIND_IP.*-b ${BG_RATE_MBIT}M" >/dev/null 2>&1; then
  echo "[ORBIT-Inspect] Background iperf already running: ${BG_RATE_MBIT} Mb/s $BG_DIRECTION via $BG_IFACE ($BG_BIND_IP), port $BG_PORT"
  exit 0
fi

echo "[ORBIT-Inspect] Clearing stale ORBIT-Inspect iperf processes on port $BG_PORT."
sudo -n pkill -f "iperf3.*-c $EDGE_IP.*-p $BG_PORT.*-B $BG_BIND_IP" >/dev/null 2>&1 || true
docker exec "$EDGE_CONTAINER" python3 - "$BG_PORT" <<'PY' >/dev/null 2>&1 || true
import os
import signal
import sys

port = sys.argv[1]
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    cmdline_path = f"/proc/{name}/cmdline"
    try:
        raw = open(cmdline_path, "rb").read()
    except OSError:
        continue
    parts = [p.decode("utf-8", "ignore") for p in raw.split(b"\0") if p]
    if not parts:
        continue
    joined = " ".join(parts)
    if "iperf3" not in parts[0] and " iperf3 " not in f" {joined} ":
        continue
    if "-s" in parts and "-p" in parts:
        try:
            if parts[parts.index("-p") + 1] == port:
                os.kill(int(name), signal.SIGTERM)
        except (IndexError, ProcessLookupError, PermissionError, ValueError):
            pass
PY

docker exec -d "$EDGE_CONTAINER" sh -lc \
  "mkdir -p '$CONTAINER_LOG_DIR' && iperf3 -s -1 -p '$BG_PORT' >> '$SERVER_LOG' 2>&1"

sleep 1

CLIENT_CMD=(
  sudo -n iperf3
  -c "$EDGE_IP"
  -p "$BG_PORT"
  -B "$BG_BIND_IP"
  -u
  -b "${BG_RATE_MBIT}M"
  -t "$BG_DURATION_SEC"
  -i 1
  --json
)

if [ "$BG_DIRECTION" = "dl" ]; then
  CLIENT_CMD+=(-R)
fi

nohup "${CLIENT_CMD[@]}" > "$LOG_DIR/background_iperf_client_stdout.json" 2>&1 &
sleep 2

if pgrep -f "iperf3.*-c $EDGE_IP.*-p $BG_PORT.*-B $BG_BIND_IP.*-b ${BG_RATE_MBIT}M" >/dev/null 2>&1; then
  echo "[ORBIT-Inspect] Background iperf running: ${BG_RATE_MBIT} Mb/s $BG_DIRECTION via $BG_IFACE ($BG_BIND_IP), port $BG_PORT"
else
  echo "[ORBIT-Inspect ERROR] Background iperf client did not stay running."
  echo "Recent client log:"
  tail -80 "$LOG_DIR/background_iperf_client_stdout.json" || true
  echo "Recent server log:"
  docker exec "$EDGE_CONTAINER" sh -lc "tail -80 '$SERVER_LOG'" || true
  echo "Container repo path used for background iperf: $CONTAINER_REPO_DIR"
  exit 1
fi
