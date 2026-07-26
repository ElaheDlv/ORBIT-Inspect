#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

EDGE_CONTAINER="${ORBIT3C_EDGE_CONTAINER_NAME:-edge-dev}"
EDGE_IP="${ORBIT3C_EDGE_IP:-192.168.83.150}"
EDGE_PORT="${ORBIT3C_EDGE_PORT:-9100}"
UE_PORT="${ORBIT3C_UE_FORWARDER_PORT:-9200}"
MODEL_PATH="${ORBIT3C_MODEL_PATH:-best_n.pt}"
RESULTS_DIR="${ORBIT_INSPECT_RESULTS_DIR:-$PWD/orbit_inspect_testbed/live_results/latest}"
LOG_DIR="$RESULTS_DIR/logs"
BG_RATE_MBIT="${ORBIT3C_BG_IPERF_MBIT:-30}"
START_BACKGROUND="${ORBIT_INSPECT_START_BACKGROUND:-1}"
RUN_ID="${ORBIT_INSPECT_RUN_ID:-latest}"
RUN_SPEED_VALUE="${ORBIT_INSPECT_RUN_SPEED:-${RUN_SPEED:-1.0}}"
EDGE_CPUS_VALUE="${ORBIT_INSPECT_EDGE_CPUS:-${EDGE_CPUS:-}}"
EDGE_COMPUTE_CAPACITY_VALUE="${ORBIT_INSPECT_EDGE_COMPUTE_CAPACITY:-${EDGE_COMPUTE_CAPACITY:-}}"
EDGE_LABEL_VALUE="${ORBIT_INSPECT_EDGE_LABEL:-${EDGE_LABEL:-}}"
EDGE_RESOURCE_CONTROL_VALUE="${ORBIT_INSPECT_EDGE_RESOURCE_CONTROL:-${EDGE_RESOURCE_CONTROL:-docker_update_cpus}}"
EDGE_TORCH_THREADS_VALUE="${EDGE_TORCH_THREADS:-4}"
EDGE_RUN_DIR_REL="${ORBIT_INSPECT_EDGE_RUN_DIR_REL:-orbit_inspect_testbed/live_results/latest/edge_runs/$RUN_ID}"
EDGE_RUN_DIR_HOST="$PWD/$EDGE_RUN_DIR_REL"
EDGE_RUNS_ROOT_HOST="$(dirname "$EDGE_RUN_DIR_HOST")"
EDGE_STDOUT_HOST="$EDGE_RUN_DIR_HOST/edge_stdout.txt"
EDGE_START_TIMEOUT_SEC="${ORBIT_INSPECT_EDGE_START_TIMEOUT_SEC:-60}"
FACTORY_PID_FILE="$LOG_DIR/factory_relay.pid"
CONTAINER_REPO_DIR="${ORBIT_INSPECT_CONTAINER_REPO_DIR:-}"

prepare_shared_edge_dir() {
  local path="$1"
  mkdir -p "$path"
  chmod a+rwx "$path"
}

mkdir -p "$LOG_DIR"
if [ -e "$EDGE_RUNS_ROOT_HOST" ] && [ ! -w "$EDGE_RUNS_ROOT_HOST" ]; then
  FALLBACK_EDGE_RUN_DIR_REL="orbit_inspect_testbed/live_results/latest/logs/edge_runs/$RUN_ID"
  echo "[ORBIT-Inspect WARN] $EDGE_RUNS_ROOT_HOST is not writable by the host user."
  echo "[ORBIT-Inspect WARN] Using fallback edge output directory: $FALLBACK_EDGE_RUN_DIR_REL"
  EDGE_RUN_DIR_REL="$FALLBACK_EDGE_RUN_DIR_REL"
  EDGE_RUN_DIR_CONTAINER="/workspace/$EDGE_RUN_DIR_REL"
  EDGE_RUN_DIR_HOST="$PWD/$EDGE_RUN_DIR_REL"
  EDGE_RUNS_ROOT_HOST="$(dirname "$EDGE_RUN_DIR_HOST")"
  EDGE_STDOUT_HOST="$EDGE_RUN_DIR_HOST/edge_stdout.txt"
fi
prepare_shared_edge_dir "$EDGE_RUNS_ROOT_HOST"
prepare_shared_edge_dir "$EDGE_RUN_DIR_HOST"

tcp_ready() {
  python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1.0); s.connect((sys.argv[1], int(sys.argv[2]))); s.close()" "$1" "$2" >/dev/null 2>&1
}

factory_pid_running() {
  [ -s "$FACTORY_PID_FILE" ] || return 1
  local pid
  pid="$(cat "$FACTORY_PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "factory_relay_orbit_inspect_live.py"
}

stop_factory_relay() {
  if factory_pid_running; then
    local pid
    pid="$(cat "$FACTORY_PID_FILE")"
    kill "$pid" >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5; do
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        return 0
      fi
      sleep 0.4
    done
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
  pkill -f "factory_relay_orbit_inspect_live.py" >/dev/null 2>&1 || true
}

echo "[ORBIT-Inspect] Starting/checking application-side live stack."
echo "[ORBIT-Inspect] Logs: $LOG_DIR"

if ! docker ps --format '{{.Names}}' | grep -qx "$EDGE_CONTAINER"; then
  echo "[ORBIT-Inspect ERROR] Docker container $EDGE_CONTAINER is not running."
  echo "Start/create it first using OAI_DEV_FULL_SETUP_GUIDE.md section 12."
  exit 1
fi

if [ -z "$CONTAINER_REPO_DIR" ]; then
  REPO_BASENAME="$(basename "$PWD")"
  if docker exec "$EDGE_CONTAINER" test -f "/workspace/$REPO_BASENAME/edge_relay_oai_dev.py" >/dev/null 2>&1; then
    CONTAINER_REPO_DIR="/workspace/$REPO_BASENAME"
  else
    CONTAINER_REPO_DIR="/workspace"
  fi
fi
EDGE_RUN_DIR_CONTAINER="$CONTAINER_REPO_DIR/$EDGE_RUN_DIR_REL"

if [ "${ORBIT_INSPECT_RESTART_EDGE:-0}" = "1" ]; then
  echo "[ORBIT-Inspect] Restarting edge relay inside $EDGE_CONTAINER by request."
  docker exec "$EDGE_CONTAINER" python3 "$CONTAINER_REPO_DIR/orbit_inspect_testbed/runtime_scripts/edge_relay_process_ctl.py" stop --timeout-sec 3.0
  sleep 1
fi

if [ "${ORBIT_INSPECT_RESTART_EDGE:-0}" = "1" ] && tcp_ready "$EDGE_IP" "$EDGE_PORT"; then
  echo "[ORBIT-Inspect ERROR] Edge relay port is still reachable after restart cleanup."
  echo "A stale edge relay or another process may still own $EDGE_IP:$EDGE_PORT; stop it before running live."
  docker exec "$EDGE_CONTAINER" python3 "$CONTAINER_REPO_DIR/orbit_inspect_testbed/runtime_scripts/edge_relay_process_ctl.py" list || true
  exit 1
fi

if tcp_ready "$EDGE_IP" "$EDGE_PORT"; then
  echo "[ORBIT-Inspect] Edge relay already reachable at $EDGE_IP:$EDGE_PORT"
else
  echo "[ORBIT-Inspect] Starting edge relay inside $EDGE_CONTAINER"
  docker exec -d "$EDGE_CONTAINER" bash -lc "
set -e
cd $CONTAINER_REPO_DIR
mkdir -p $EDGE_RUN_DIR_CONTAINER
echo '[ORBIT-Inspect EDGE] Launching edge_relay_oai_dev.py' >> $EDGE_RUN_DIR_CONTAINER/edge_stdout.txt
MODEL_PATH=$CONTAINER_REPO_DIR/$MODEL_PATH \
RUN_SPEED=$RUN_SPEED_VALUE \
EDGE_CSV_PATH=$EDGE_RUN_DIR_CONTAINER/edge_results.csv \
EDGE_DEBUG_DIR=$EDGE_RUN_DIR_CONTAINER/edge_debug \
EDGE_ROI_DEBUG_DIR=$EDGE_RUN_DIR_CONTAINER/edge_roi_debug \
EDGE_USE_BACKGROUND_ROI=1 \
EDGE_ROTATE_ROI_BEFORE_INFERENCE=1 \
EDGE_ROI_ROTATION_MODE=cw90 \
EDGE_LABEL=$EDGE_LABEL_VALUE \
EDGE_CPUS=$EDGE_CPUS_VALUE \
EDGE_COMPUTE_CAPACITY=$EDGE_COMPUTE_CAPACITY_VALUE \
EDGE_RESOURCE_CONTROL=$EDGE_RESOURCE_CONTROL_VALUE \
EDGE_TORCH_THREADS=$EDGE_TORCH_THREADS_VALUE \
EDGE_PRELOAD_MODEL=${EDGE_PRELOAD_MODEL:-1} \
EDGE_WARMUP_INFERENCE=${EDGE_WARMUP_INFERENCE:-1} \
EDGE_WARMUP_IMAGE_WIDTH=${EDGE_WARMUP_IMAGE_WIDTH:-640} \
EDGE_WARMUP_IMAGE_HEIGHT=${EDGE_WARMUP_IMAGE_HEIGHT:-640} \
EDGE_LISTEN_IP=0.0.0.0 \
EDGE_LISTEN_PORT=$EDGE_PORT \
PYTHONUNBUFFERED=1 \
python3 $CONTAINER_REPO_DIR/edge_relay_oai_dev.py >> $EDGE_RUN_DIR_CONTAINER/edge_stdout.txt 2>&1
"
fi

EDGE_DEAD=0
for attempt in $(seq 1 "$EDGE_START_TIMEOUT_SEC"); do
  if tcp_ready "$EDGE_IP" "$EDGE_PORT"; then
    echo "[ORBIT-Inspect] Edge relay is reachable."
    EDGE_DEAD=0
    break
  fi
  if [ "$attempt" -gt 5 ] && ! docker exec "$EDGE_CONTAINER" python3 "$CONTAINER_REPO_DIR/orbit_inspect_testbed/runtime_scripts/edge_relay_process_ctl.py" list >/dev/null 2>&1; then
    EDGE_DEAD=1
    break
  fi
  sleep 1
done

if ! tcp_ready "$EDGE_IP" "$EDGE_PORT"; then
  echo "[ORBIT-Inspect ERROR] Edge relay did not become reachable at $EDGE_IP:$EDGE_PORT within ${EDGE_START_TIMEOUT_SEC}s."
  if [ "$EDGE_DEAD" = "1" ]; then
    echo "[ORBIT-Inspect ERROR] Edge relay process exited during startup."
  fi
  echo "Recent edge log: $EDGE_STDOUT_HOST"
  tail -80 "$EDGE_STDOUT_HOST" || true
  exit 1
fi

if ! ip link show "${ORBIT3C_UE_BIND_INTERFACE:-oaitun_ue1}" >/dev/null 2>&1; then
  echo "[ORBIT-Inspect ERROR] ${ORBIT3C_UE_BIND_INTERFACE:-oaitun_ue1} is missing."
  echo "Recover/start OAI gNB and nrUE first: automated_experiments/scripts/recover_oai_stack.sh"
  exit 1
fi

if tcp_ready "127.0.0.1" "$UE_PORT"; then
  echo "[ORBIT-Inspect] UE forwarder already listening on 127.0.0.1:$UE_PORT"
else
  echo "[ORBIT-Inspect] Starting UE forwarder on host. This needs cached sudo."
  ./orbit_inspect_testbed/start_ue_forwarder_live.sh
fi

if tcp_ready "127.0.0.1" "$UE_PORT"; then
  echo "[ORBIT-Inspect] UE forwarder is listening."
else
  echo "[ORBIT-Inspect ERROR] UE forwarder did not become reachable at 127.0.0.1:$UE_PORT."
  echo "Check $LOG_DIR/ue_forwarder_stdout.txt."
  exit 1
fi

if [ "${ORBIT_INSPECT_RESTART_FACTORY:-0}" = "1" ]; then
  echo "[ORBIT-Inspect] Restarting ORBIT-Inspect factory relay by request."
  stop_factory_relay
  sleep 1
fi

if factory_pid_running; then
  echo "[ORBIT-Inspect] ORBIT-Inspect factory relay is already running."
else
  echo "[ORBIT-Inspect] Starting ORBIT-Inspect factory relay."
  nohup setsid ./orbit_inspect_testbed/start_factory_relay_live.sh > "$LOG_DIR/factory_stdout.txt" 2>&1 &
  echo "$!" > "$FACTORY_PID_FILE"
  for _ in 1 2 3 4 5; do
    if factory_pid_running; then
      break
    fi
    sleep 1
  done
fi

if factory_pid_running; then
  echo "[ORBIT-Inspect] ORBIT-Inspect factory relay is running."
else
  echo "[ORBIT-Inspect ERROR] ORBIT-Inspect factory relay is not running."
  echo "Recent factory log:"
  tail -80 "$LOG_DIR/factory_stdout.txt" || true
  exit 1
fi

if [ "$START_BACKGROUND" != "1" ]; then
  echo "[ORBIT-Inspect] Background traffic start/check skipped by ORBIT_INSPECT_START_BACKGROUND=$START_BACKGROUND."
elif [ -n "$BG_RATE_MBIT" ] && [ "$BG_RATE_MBIT" != "0" ]; then
  echo "[ORBIT-Inspect] Starting/checking ${BG_RATE_MBIT} Mb/s background UL traffic."
  ./orbit_inspect_testbed/start_background_iperf_live.sh
else
  echo "[ORBIT-Inspect] Background traffic disabled."
fi

echo "[ORBIT-Inspect] Application-side live stack check/start complete."
echo "[ORBIT-Inspect] Run ./orbit_inspect_testbed/check_live_stack.sh for full preflight."
