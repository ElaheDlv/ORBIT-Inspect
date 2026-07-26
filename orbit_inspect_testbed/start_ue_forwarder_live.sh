#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

UE_PORT="${ORBIT3C_UE_FORWARDER_PORT:-9200}"
UE_BIND_INTERFACE="${ORBIT3C_UE_BIND_INTERFACE:-oaitun_ue1}"
RESULTS_DIR="${ORBIT_INSPECT_RESULTS_DIR:-$PWD/orbit_inspect_testbed/live_results/latest}"
LOG_DIR="$RESULTS_DIR/logs"
LOG_PATH="$LOG_DIR/ue_forwarder_stdout.txt"

mkdir -p "$LOG_DIR"

tcp_ready() {
  python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1.0); s.connect((sys.argv[1], int(sys.argv[2]))); s.close()" "$1" "$2" >/dev/null 2>&1
}

if tcp_ready "127.0.0.1" "$UE_PORT"; then
  echo "[ORBIT-Inspect] UE forwarder already listening on 127.0.0.1:$UE_PORT"
  exit 0
fi

if ! ip link show "$UE_BIND_INTERFACE" >/dev/null 2>&1; then
  echo "[ORBIT-Inspect ERROR] $UE_BIND_INTERFACE is missing."
  echo "Recover/start OAI gNB and nrUE first."
  exit 1
fi

if ! sudo -n true; then
  echo "[ORBIT-Inspect ERROR] sudo cache is not available. Run sudo -v, then rerun this script."
  exit 1
fi

if pgrep -f "orbit_inspect_testbed/ue_forwarder_live_supervisor.sh" >/dev/null 2>&1; then
  echo "[ORBIT-Inspect] Replacing existing UE forwarder supervisor because port $UE_PORT is not reachable."
  pkill -f "orbit_inspect_testbed/ue_forwarder_live_supervisor.sh" >/dev/null 2>&1 || true
  sudo -n pkill -f "orbit_inspect_testbed/ue_forwarder_live_supervisor.sh" >/dev/null 2>&1 || true
  sleep 1
fi

echo "[ORBIT-Inspect] Starting supervised UE forwarder as root."
nohup sudo -n env \
  EDGE_IP="${ORBIT3C_EDGE_IP:-192.168.83.150}" \
  EDGE_PORT="${ORBIT3C_EDGE_PORT:-9100}" \
  UE_BIND_INTERFACE="$UE_BIND_INTERFACE" \
  SOCKET_TIMEOUT_SEC="${SOCKET_TIMEOUT_SEC:-120.0}" \
  ./orbit_inspect_testbed/ue_forwarder_live_supervisor.sh > "$LOG_PATH" 2>&1 &

for _ in 1 2 3 4 5; do
  sleep 1
  if tcp_ready "127.0.0.1" "$UE_PORT"; then
    echo "[ORBIT-Inspect] UE forwarder is listening on 127.0.0.1:$UE_PORT"
    exit 0
  fi
done

echo "[ORBIT-Inspect ERROR] UE forwarder did not become reachable at 127.0.0.1:$UE_PORT."
echo "Recent UE forwarder log:"
tail -120 "$LOG_PATH" || true
exit 1
