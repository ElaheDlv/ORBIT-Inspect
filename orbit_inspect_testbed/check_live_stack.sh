#!/usr/bin/env bash
set -u

cd "$(dirname "$0")/.."

EDGE_CONTAINER="${ORBIT3C_EDGE_CONTAINER_NAME:-edge-dev}"
EDGE_IP="${ORBIT3C_EDGE_IP:-192.168.83.150}"
EDGE_PORT="${ORBIT3C_EDGE_PORT:-9100}"
UE_PORT="${ORBIT3C_UE_FORWARDER_PORT:-9200}"
LIVE_RNTI_FILE="${ORBIT3C_LIVE_RNTI_FILE:-orbit_inspect_testbed/live_results/latest/live_rntis.json}"
SLICE_XAPP="${ORBIT3C_SLICE_XAPP:-/home/elahe/user/ORBIT3C_OAI_DEV/oai_ran/openairinterface5g/openair2/E2AP/flexric/build/examples/xApp/c/slice/xapp_orbit3c_static_slice_ctrl}"
MEASURED_CONFIG_DIR="${ORBIT3C_MEASURED_CONFIG_DIR:-${ORBIT3C_CONFIG_DIR:-/home/elahe/user/ORBIT3C_OAI_DEV/configs}}"
BG_RATE_MBIT="${ORBIT3C_BG_IPERF_MBIT:-30}"
BG_IFACE="${ORBIT3C_BG_TUN_IFACE:-oaitun_ue2}"
BG_PORT="${ORBIT3C_BG_IPERF_PORT:-5203}"
FACTORY_PID_FILE="${ORBIT_INSPECT_FACTORY_PID_FILE:-orbit_inspect_testbed/live_results/latest/logs/factory_relay.pid}"
CONTAINER_REPO_DIR="${ORBIT_INSPECT_CONTAINER_REPO_DIR:-}"

FAILURES=0
WARNINGS=0

pass() {
  printf '[OK]   %s\n' "$1"
}

warn() {
  WARNINGS=$((WARNINGS + 1))
  printf '[WARN] %s\n' "$1"
}

fail() {
  FAILURES=$((FAILURES + 1))
  printf '[MISS] %s\n' "$1"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

check_process() {
  local label="$1"
  local pattern="$2"
  local hint="${3:-}"
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    pass "$label process is running"
  else
    if [ -n "$hint" ]; then
      fail "$label process is not running; check $hint"
    else
      fail "$label process is not running"
    fi
  fi
}

check_factory_relay() {
  if [ -s "$FACTORY_PID_FILE" ]; then
    local pid
    pid="$(cat "$FACTORY_PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1 && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "factory_relay_orbit_inspect_live.py"; then
      pass "ORBIT-Inspect factory relay process is running"
      return
    fi
    fail "ORBIT-Inspect factory relay PID file exists but process is not running; check orbit_inspect_testbed/live_results/latest/logs/factory_stdout.txt"
    return
  fi
  check_process "ORBIT-Inspect factory relay" "factory_relay_orbit_inspect_live.py" "orbit_inspect_testbed/live_results/latest/logs/factory_stdout.txt"
}

check_tcp() {
  local label="$1"
  local host="$2"
  local port="$3"
  if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1.5); s.connect((sys.argv[1], int(sys.argv[2]))); s.close()" "$host" "$port" >/dev/null 2>&1; then
    pass "$label TCP endpoint is reachable at $host:$port"
  else
    fail "$label TCP endpoint is not reachable at $host:$port"
  fi
}

config_enables_chanmod() {
  /usr/bin/python3 - "$1" <<'PY'
import re
import sys

for line in open(sys.argv[1], encoding="utf-8"):
    code = line.split("#", 1)[0]
    if re.search(r"\boptions\s*=\s*.*\bchanmod\b", code):
        sys.exit(0)
sys.exit(1)
PY
}

echo "ORBIT-Inspect live-stack preflight"
echo "Repository: $PWD"
echo

GNB_CONFIG="$MEASURED_CONFIG_DIR/gnb_orbit3c_dev_rfsim_e2.conf"
UE_CONFIG="$MEASURED_CONFIG_DIR/ue_orbit3c_dev_rfsim_2ue.conf"
if [ -f "$GNB_CONFIG" ] && [ -f "$UE_CONFIG" ]; then
  if config_enables_chanmod "$GNB_CONFIG"; then
    fail "gNB config enables RFsim chanmod; run reset_measured_channel_stack.sh with the measured/default config"
  else
    pass "gNB config has RFsim chanmod disabled"
  fi
  if config_enables_chanmod "$UE_CONFIG"; then
    fail "UE config enables RFsim chanmod; run reset_measured_channel_stack.sh with the measured/default config"
  else
    pass "UE config has RFsim chanmod disabled"
  fi
else
  fail "measured/default gNB or UE config is missing in $MEASURED_CONFIG_DIR"
fi

if have_cmd docker; then
  if docker ps --format '{{.Names}}' | grep -qx "$EDGE_CONTAINER"; then
    pass "Docker container $EDGE_CONTAINER is running"
    if [ -z "$CONTAINER_REPO_DIR" ]; then
      REPO_BASENAME="$(basename "$PWD")"
      if docker exec "$EDGE_CONTAINER" test -f "/workspace/$REPO_BASENAME/edge_relay_oai_dev.py" >/dev/null 2>&1; then
        CONTAINER_REPO_DIR="/workspace/$REPO_BASENAME"
      elif docker exec "$EDGE_CONTAINER" test -f "/workspace/edge_relay_oai_dev.py" >/dev/null 2>&1; then
        CONTAINER_REPO_DIR="/workspace"
      fi
    fi
  else
    fail "Docker container $EDGE_CONTAINER is not running"
  fi
  if docker inspect "$EDGE_CONTAINER" >/dev/null 2>&1; then
    pass "Docker CPU control can inspect $EDGE_CONTAINER"
  else
    fail "Docker CPU control cannot inspect $EDGE_CONTAINER"
  fi
else
  fail "docker command is not available"
fi

if [ -x "$SLICE_XAPP" ]; then
  pass "FlexRIC slice xApp exists"
else
  fail "FlexRIC slice xApp is missing or not executable: $SLICE_XAPP"
fi

if [ -n "${ORBIT3C_SLICE_RNTI:-}" ] && [ -n "${ORBIT3C_BG_RNTI:-}" ]; then
  pass "Live PRB RNTIs are set in environment"
elif [ -s "$LIVE_RNTI_FILE" ]; then
  pass "Live PRB RNTIs are configured in $LIVE_RNTI_FILE"
else
  fail "Live PRB RNTIs are not configured; run orbit_inspect_testbed/discover_live_rntis.py"
fi

if ip link show oaitun_ue1 >/dev/null 2>&1; then
  pass "oaitun_ue1 exists"
else
  fail "oaitun_ue1 does not exist"
fi

if ip link show oaitun_ue2 >/dev/null 2>&1; then
  pass "oaitun_ue2 exists"
else
  warn "oaitun_ue2 does not exist; this is acceptable only for one-UE/no-background demos"
fi

if [ -n "$BG_RATE_MBIT" ] && [ "$BG_RATE_MBIT" != "0" ]; then
  if pgrep -f "iperf3.*-p $BG_PORT.*-b ${BG_RATE_MBIT}M" >/dev/null 2>&1; then
    pass "${BG_RATE_MBIT} Mb/s background iperf process is running for $BG_IFACE on port $BG_PORT"
  else
    fail "${BG_RATE_MBIT} Mb/s background iperf process is not running for $BG_IFACE on port $BG_PORT; run orbit_inspect_testbed/start_background_iperf_live.sh"
  fi
else
  warn "background iperf is disabled by ORBIT3C_BG_IPERF_MBIT=$BG_RATE_MBIT"
fi

check_tcp "edge relay" "$EDGE_IP" "$EDGE_PORT"
check_tcp "UE forwarder" "127.0.0.1" "$UE_PORT"

if have_cmd docker && docker ps --format '{{.Names}}' | grep -qx "$EDGE_CONTAINER"; then
  if [ -n "$CONTAINER_REPO_DIR" ]; then
    EDGE_ENV="$(docker exec "$EDGE_CONTAINER" python3 "$CONTAINER_REPO_DIR/orbit_inspect_testbed/runtime_scripts/edge_relay_process_ctl.py" env 2>/dev/null || true)"
  else
    EDGE_ENV=""
  fi
  EDGE_USE_BACKGROUND_ROI="$(printf '%s\n' "$EDGE_ENV" | awk -F= 'tolower($1)=="edge_use_background_roi"{print tolower($2); exit}')"
  EDGE_ROTATE_ROI="$(printf '%s\n' "$EDGE_ENV" | awk -F= 'tolower($1)=="edge_rotate_roi_before_inference"{print tolower($2); exit}')"
  if [ -z "$EDGE_ENV" ]; then
    fail "no edge_relay_oai_dev.py process was found inside $EDGE_CONTAINER"
  elif printf '%s\n' "$EDGE_USE_BACKGROUND_ROI" | grep -Eq '^(0|false|no)$'; then
    fail "edge relay is running with EDGE_USE_BACKGROUND_ROI=$EDGE_USE_BACKGROUND_ROI; restart with ORBIT_INSPECT_RESTART_EDGE=1"
  else
    pass "edge relay uses experiment-runner edge ROI default"
  fi
  if printf '%s\n' "$EDGE_ROTATE_ROI" | grep -Eq '^(0|false|no)$'; then
    fail "edge relay is running with EDGE_ROTATE_ROI_BEFORE_INFERENCE=$EDGE_ROTATE_ROI; restart with ORBIT_INSPECT_RESTART_EDGE=1"
  else
    pass "edge relay uses experiment-runner ROI rotation default"
  fi
fi

LATEST_FACTORY_CSV="$(find orbit_inspect_testbed/live_results/latest -maxdepth 1 -type f -name 'factory_latency_*.csv' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
if [ -n "$LATEST_FACTORY_CSV" ]; then
  LATEST_EDGE_ROI_STATUS="$(/usr/bin/python3 - "$LATEST_FACTORY_CSV" <<'PY'
import csv
import sys

try:
    with open(sys.argv[1], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
except Exception:
    rows = []

for row in reversed(rows):
    status = (row.get("edge_roi_status") or "").strip()
    if status:
        print(status)
        break
PY
)"
  if printf '%s\n' "$LATEST_EDGE_ROI_STATUS" | grep -q '^edge_roi_disabled'; then
    warn "latest old live CSV reports $LATEST_EDGE_ROI_STATUS; run Apply Live once after restarting edge before trusting model-miss results"
  elif [ -n "$LATEST_EDGE_ROI_STATUS" ]; then
    pass "latest live CSV edge ROI status is $LATEST_EDGE_ROI_STATUS"
  fi
fi

check_process "nearRT-RIC" "nearRT-RIC"
check_process "OAI gNB" "nr-softmodem"
check_process "OAI nrUE" "nr-uesoftmodem"
check_factory_relay

if have_cmd ros2; then
  PROBE_OUTPUT="$("${ORBIT3C_ROS_PYTHON:-/usr/bin/python3}" orbit_inspect_testbed/probe_live_ros.py 2>/dev/null)"
  PROBE_STATUS=$?
  if [ "$PROBE_STATUS" -eq 0 ] && printf '%s' "$PROBE_OUTPUT" | grep -q '"isaac"'; then
    pass "Isaac acknowledged /orbit_inspect/control probe"
  else
    fail "Isaac did not acknowledge /orbit_inspect/control probe"
  fi

  if printf '%s' "$PROBE_OUTPUT" | grep -q '"factory_relay"'; then
    pass "factory relay acknowledged /orbit_inspect/control probe"
  else
    fail "factory relay did not acknowledge /orbit_inspect/control probe"
  fi

  if ros2 topic info /fortuna_inspection/request 2>/dev/null | grep -q 'Subscription count: [1-9]'; then
    pass "factory relay is subscribed to /fortuna_inspection/request"
  else
    fail "factory relay is not visible on /fortuna_inspection/request"
  fi
else
  warn "ros2 command is not available; source /opt/ros/jazzy/setup.bash before checking ROS topics"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "Preflight passed with $WARNINGS warning(s)."
  exit 0
fi

echo "Preflight found $FAILURES missing item(s) and $WARNINGS warning(s)."
echo
echo "Typical recovery order:"
echo "  1. Reset measured radio/core path: ./orbit_inspect_testbed/reset_measured_channel_stack.sh"
echo "  2. Start app-side live pieces:     ./orbit_inspect_testbed/start_live_application_stack.sh"
echo "  3. Start dashboard backend:        ./orbit_inspect_testbed/start_live_backend.sh"
echo "  4. Run the ORBIT-Inspect Isaac script inside Isaac Sim."
echo
echo "For only the UE forwarder missing item, run:"
echo "  ./orbit_inspect_testbed/start_ue_forwarder_live.sh"
exit 1
