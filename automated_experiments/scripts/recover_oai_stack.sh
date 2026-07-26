#!/usr/bin/env bash
set -euo pipefail

# Restart the OAI/FlexRIC two-UE RFsim stack used by the PRB slicing runner.
# Defaults match PRB_Slicing_Two_UE_Run_Guide_UPDATED.md.
#
# Required:
#   sudo -v
#   run as normal user, not root
#
# Options:
#   ORBIT3C_RECOVERY_DRY_RUN=1
#   ORBIT3C_RECOVER_CORE=1
#   ORBIT3C_CORE_RECOVERY_MODE=recreate|restart
#   ORBIT3C_RECOVERY_BACKEND=auto|tmux|nohup

OAI_DEV_DIR="${ORBIT3C_OAI_DEV_DIR:-/home/elahe/user/ORBIT3C_OAI_DEV}"
OAI_RAN_DIR="${ORBIT3C_OAI_RAN_DIR:-$OAI_DEV_DIR/oai_ran/openairinterface5g}"
FLEXRIC_DIR="${ORBIT3C_FLEXRIC_DIR:-$OAI_RAN_DIR/openair2/E2AP/flexric}"
RUN_BUILD_DIR="${ORBIT3C_RAN_BUILD_DIR:-$OAI_RAN_DIR/cmake_targets/ran_build/build}"
CONFIG_DIR="${ORBIT3C_CONFIG_DIR:-$OAI_DEV_DIR/configs}"
LOG_DIR="${ORBIT3C_LOG_DIR:-$OAI_DEV_DIR/logs}"
COMPOSE_DIR="${ORBIT3C_CORE_COMPOSE_DIR:-$OAI_DEV_DIR/dev_core/docker-compose-dev}"
COMPOSE_FILE="${ORBIT3C_CORE_COMPOSE_FILE:-docker-compose-orbit3c-dev.yaml}"
CORE_MYSQL_SERVICE="${ORBIT3C_CORE_MYSQL_SERVICE:-mysql}"
CORE_SMF_SERVICE="${ORBIT3C_CORE_SMF_SERVICE:-oai-smf}"
CORE_AMF_SERVICE="${ORBIT3C_CORE_AMF_SERVICE:-oai-amf}"
CORE_UPF_SERVICE="${ORBIT3C_CORE_UPF_SERVICE:-vpp-upf}"
CORE_NRF_SERVICE="${ORBIT3C_CORE_NRF_SERVICE:-oai-nrf}"
CORE_UDR_SERVICE="${ORBIT3C_CORE_UDR_SERVICE:-oai-udr}"
CORE_UDM_SERVICE="${ORBIT3C_CORE_UDM_SERVICE:-oai-udm}"
CORE_AUSF_SERVICE="${ORBIT3C_CORE_AUSF_SERVICE:-oai-ausf}"
CORE_EXT_DN_SERVICE="${ORBIT3C_CORE_EXT_DN_SERVICE:-oai-ext-dn}"
CORE_MYSQL_CONTAINER="${ORBIT3C_CORE_MYSQL_CONTAINER:-mysql-dev}"
CORE_AMF_CONTAINER="${ORBIT3C_CORE_AMF_CONTAINER:-oai-amf-dev}"
CORE_SMF_CONTAINER="${ORBIT3C_CORE_SMF_CONTAINER:-oai-smf-dev}"
CORE_UPF_CONTAINER="${ORBIT3C_CORE_UPF_CONTAINER:-vpp-upf-dev}"
CORE_NRF_CONTAINER="${ORBIT3C_CORE_NRF_CONTAINER:-oai-nrf-dev}"
CORE_UDR_CONTAINER="${ORBIT3C_CORE_UDR_CONTAINER:-oai-udr-dev}"
CORE_UDM_CONTAINER="${ORBIT3C_CORE_UDM_CONTAINER:-oai-udm-dev}"
CORE_AUSF_CONTAINER="${ORBIT3C_CORE_AUSF_CONTAINER:-oai-ausf-dev}"
CORE_EXT_DN_CONTAINER="${ORBIT3C_CORE_EXT_DN_CONTAINER:-oai-ext-dn-dev}"
EDGE_CONTAINER_NAME="${ORBIT3C_EDGE_CONTAINER_NAME:-edge-dev}"
EDGE_ROUTE_DEV="${ORBIT3C_EDGE_ROUTE_DEV:-eth0}"
UE_RETURN_ROUTE="${ORBIT3C_UE_RETURN_ROUTE:-12.1.1.128/25}"
UPF_N6_GATEWAY="${ORBIT3C_UPF_N6_GATEWAY:-192.168.83.201}"
CONNECTIVITY_CHECK="${ORBIT3C_RECOVERY_CONNECTIVITY_CHECK:-tcp}"
CONNECTIVITY_TEST_PORT="${ORBIT3C_RECOVERY_CONNECTIVITY_TEST_PORT:-5209}"
CONNECTIVITY_TEST_TIMEOUT_SEC="${ORBIT3C_RECOVERY_CONNECTIVITY_TEST_TIMEOUT_SEC:-20}"
VALIDATE_UPF_SESSIONS="${ORBIT3C_VALIDATE_UPF_SESSIONS:-1}"
VPPCTL_PATH="${ORBIT3C_VPPCTL_PATH:-/openair-upf/bin/vppctl}"
UPF_SESSION_WAIT_TIMEOUT_SEC="${ORBIT3C_UPF_SESSION_WAIT_TIMEOUT_SEC:-90}"

RIC_SESSION="${ORBIT3C_RECOVERY_RIC_SESSION:-orbit3c-ric}"
GNB_SESSION="${ORBIT3C_RECOVERY_GNB_SESSION:-orbit3c-gnb}"
UE_SESSION="${ORBIT3C_RECOVERY_UE_SESSION:-orbit3c-ue}"
BACKEND="${ORBIT3C_RECOVERY_BACKEND:-auto}"

RECOVER_CORE="${ORBIT3C_RECOVER_CORE:-0}"
CORE_RECOVERY_MODE="${ORBIT3C_CORE_RECOVERY_MODE:-recreate}"
CORE_RECOVERY_SCOPE="${ORBIT3C_CORE_RECOVERY_SCOPE:-full}"
DRY_RUN="${ORBIT3C_RECOVERY_DRY_RUN:-0}"
EDGE_IP="${ORBIT3C_EDGE_IP:-192.168.83.150}"
WAIT_TIMEOUT_SEC="${ORBIT3C_RECOVERY_WAIT_TIMEOUT_SEC:-120}"
POST_CORE_RESTART_SLEEP_SEC="${ORBIT3C_POST_CORE_RESTART_SLEEP_SEC:-15}"
UE_START_ATTEMPTS="${ORBIT3C_UE_START_ATTEMPTS:-2}"

GNB_CMD="${ORBIT3C_GNB_CMD:-sudo -n ORBIT3C_ULSCH_GRANT_CSV=$LOG_DIR/ulsch_grants.csv ./nr-softmodem -O $CONFIG_DIR/gnb_orbit3c_dev_rfsim_e2.conf --rfsim --rfsimulator.[0].serveraddr server --gNBs.[0].min_rxtxtime 6 2>&1 | tee $LOG_DIR/gnb_dev_rfsim_e2.log}"
UE_CMD="${ORBIT3C_UE_CMD:-sudo -n ./nr-uesoftmodem -O $CONFIG_DIR/ue_orbit3c_dev_rfsim_2ue.conf --rfsim --num-ues 2 -C 3619200000 -r 106 --numerology 1 --ssb 516 --band 78 2>&1 | tee $LOG_DIR/ue_2ue_dev_rfsim_e2.log}"

run() {
  echo "[Recovery CMD] $*"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  "$@"
}

run_bash() {
  echo "[Recovery CMD] bash -lc $1"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  bash -lc "$1"
}

classify_recovery_reason() {
  local reason="${ORBIT3C_RECOVERY_REASON:-}"
  if [[ "$RECOVER_CORE" == "1" ]]; then
    return
  fi

  case "$reason" in
    *"OAI core unhealthy:"*|*"oaitun_ue"*|*"tunnel preflight:"*|*"PDU session"*|*"SMF"*|*"UPF"*|*"AMF"*)
      RECOVER_CORE="1"
      echo "[Recovery] Enabling core recovery because failure points to core/tunnel/PDU state."
      ;;
  esac
}

select_backend() {
  if [[ "$BACKEND" == "auto" ]]; then
    if command -v tmux >/dev/null 2>&1; then
      BACKEND="tmux"
    else
      BACKEND="nohup"
    fi
  fi

  if [[ "$BACKEND" != "tmux" && "$BACKEND" != "nohup" ]]; then
    echo "[Recovery ERROR] ORBIT3C_RECOVERY_BACKEND must be auto, tmux, or nohup." >&2
    exit 2
  fi

  if [[ "$BACKEND" == "tmux" ]] && ! command -v tmux >/dev/null 2>&1; then
    echo "[Recovery ERROR] tmux backend requested but tmux is not installed." >&2
    exit 2
  fi

  echo "[Recovery] backend=$BACKEND"
}

start_session() {
  local name="$1"
  local log_path="$2"
  local command="$3"

  if [[ "$DRY_RUN" != "1" ]]; then
    : > "$log_path"
    : > "$log_path.nohup"
  fi

  if [[ "$BACKEND" == "tmux" ]]; then
    run tmux new-session -d -s "$name" "$command"
    return
  fi

  echo "[Recovery CMD] nohup bash -lc $command > $log_path.nohup 2>&1 &"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  nohup bash -lc "$command" > "$log_path.nohup" 2>&1 &
  echo "[Recovery] Started $name in background with PID $!"
}

wait_for_iface() {
  local iface="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[Recovery DRY_RUN] would wait for $iface"
    return 0
  fi

  local start
  start="$(date +%s)"
  local last_report=0
  while true; do
    if ip -4 -o addr show dev "$iface" >/dev/null 2>&1; then
      ip -4 -o addr show dev "$iface"
      return 0
    fi
    local elapsed
    elapsed=$(( $(date +%s) - start ))
    if (( elapsed - last_report >= 10 )); then
      echo "[Recovery] Waiting for $iface IPv4 address (${elapsed}s/${WAIT_TIMEOUT_SEC}s)"
      last_report="$elapsed"
    fi
    if (( elapsed >= WAIT_TIMEOUT_SEC )); then
      echo "[Recovery ERROR] Timed out waiting for $iface" >&2
      echo "[Recovery DEBUG] Current oaitun/12.1.1 interfaces:" >&2
      ip -4 -o addr show | grep -E 'oaitun|12\.1\.1' >&2 || true
      echo "[Recovery DEBUG] nr-uesoftmodem processes:" >&2
      ps -ef | grep -E 'nr-uesoftmodem|nr-softmodem' | grep -v grep >&2 || true
      echo "[Recovery DEBUG] Recent UE log:" >&2
      tail -n 80 "$LOG_DIR/ue_2ue_dev_rfsim_e2.log" >&2 || true
      echo "[Recovery DEBUG] Recent UE NAS/PDU/TUN lines:" >&2
      grep -iE 'PDU|NAS|Registration|DNN|oaitun|tun|TUN|reject|fail|error' "$LOG_DIR/ue_2ue_dev_rfsim_e2.log" | tail -n 100 >&2 || true
      echo "[Recovery DEBUG] Recent gNB log:" >&2
      tail -n 80 "$LOG_DIR/gnb_dev_rfsim_e2.log" >&2 || true
      echo "[Recovery DEBUG] Core container status:" >&2
      docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.RunningFor}}' >&2 || true
      echo "[Recovery DEBUG] Recent AMF log:" >&2
      docker logs --tail=100 "$CORE_AMF_CONTAINER" >&2 || true
      echo "[Recovery DEBUG] Recent SMF log:" >&2
      docker logs --tail=100 "$CORE_SMF_CONTAINER" >&2 || true
      return 1
    fi
    sleep 2
  done
}

wait_for_log_pattern() {
  local path="$1"
  local pattern="$2"
  local label="$3"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[Recovery DRY_RUN] would wait for $label in $path matching $pattern"
    return 0
  fi

  local start
  start="$(date +%s)"
  while true; do
    if [[ -f "$path" ]] && grep -E "$pattern" "$path" >/dev/null 2>&1; then
      echo "[Recovery] $label ready"
      return 0
    fi
    if (( "$(date +%s)" - start >= WAIT_TIMEOUT_SEC )); then
      echo "[Recovery WARN] Timed out waiting for $label in $path" >&2
      return 0
    fi
    sleep 2
  done
}

wait_for_log_pattern_or_fail() {
  local path="$1"
  local pattern="$2"
  local label="$3"
  if wait_for_log_pattern "$path" "$pattern" "$label"; then
    if [[ "$DRY_RUN" == "1" ]]; then
      return 0
    fi
    if [[ -f "$path" ]] && grep -E "$pattern" "$path" >/dev/null 2>&1; then
      return 0
    fi
  fi
  echo "[Recovery ERROR] Timed out waiting for required $label in $path" >&2
  return 1
}

require_core_running_or_recovering() {
  if [[ "$DRY_RUN" == "1" || "$RECOVER_CORE" == "1" ]]; then
    return 0
  fi

  local missing=()
  local container
  for container in \
    "$CORE_MYSQL_CONTAINER" \
    "$CORE_NRF_CONTAINER" \
    "$CORE_UDR_CONTAINER" \
    "$CORE_UDM_CONTAINER" \
    "$CORE_AUSF_CONTAINER" \
    "$CORE_AMF_CONTAINER" \
    "$CORE_SMF_CONTAINER" \
    "$CORE_UPF_CONTAINER" \
    "$CORE_EXT_DN_CONTAINER"; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || echo false)" != "true" ]]; then
      missing+=("$container")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "[Recovery ERROR] OAI core is not fully running; stopped/missing container(s): ${missing[*]}" >&2
    echo "[Recovery ERROR] Start recovery with core recovery enabled, for example:" >&2
    echo "  ORBIT3C_RECOVER_CORE=1 ORBIT3C_CORE_RECOVERY_MODE=recreate $0" >&2
    return 1
  fi
}

ensure_edge_return_route() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[Recovery DRY_RUN] would ensure $EDGE_CONTAINER_NAME has route $UE_RETURN_ROUTE via $UPF_N6_GATEWAY dev $EDGE_ROUTE_DEV"
    return 0
  fi

  if ! docker inspect "$EDGE_CONTAINER_NAME" >/dev/null 2>&1; then
    echo "[Recovery WARN] Edge container $EDGE_CONTAINER_NAME not found; skipping edge return-route setup." >&2
    return 0
  fi

  local running
  running="$(docker inspect -f '{{.State.Running}}' "$EDGE_CONTAINER_NAME" 2>/dev/null || echo false)"
  if [[ "$running" != "true" ]]; then
    run docker start "$EDGE_CONTAINER_NAME"
  fi

  echo "[Recovery] Ensuring $EDGE_CONTAINER_NAME return route to UE subnet"
  run docker exec "$EDGE_CONTAINER_NAME" ip route replace "$UE_RETURN_ROUTE" via "$UPF_N6_GATEWAY" dev "$EDGE_ROUTE_DEV"
  run docker exec "$EDGE_CONTAINER_NAME" ip route show "$UE_RETURN_ROUTE"
}

cleanup_edge_iperf_port() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[Recovery DRY_RUN] would clear iperf3 listeners on $EDGE_CONTAINER_NAME:$CONNECTIVITY_TEST_PORT"
    return 0
  fi

  docker exec "$EDGE_CONTAINER_NAME" sh -lc "
    if command -v pkill >/dev/null 2>&1; then
      pkill -f 'iperf3.*-s.*-p $CONNECTIVITY_TEST_PORT' || true
    fi
    if command -v fuser >/dev/null 2>&1; then
      fuser -k ${CONNECTIVITY_TEST_PORT}/tcp || true
    fi
    if command -v ss >/dev/null 2>&1; then
      for pid in \$(ss -ltnp 2>/dev/null \
        | awk '\$4 ~ /:$CONNECTIVITY_TEST_PORT$/ {print \$NF}' \
        | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
        | sort -u); do
        kill -TERM \"\$pid\" || true
      done
    fi
  " || true
}

dump_reachability_debug() {
  echo "[Recovery DEBUG] Reachability diagnostics:" >&2
  ip -4 -o addr show dev oaitun_ue1 >&2 || true
  ip -4 -o addr show dev oaitun_ue2 >&2 || true
  ip route get "$EDGE_IP" from "$(ip -4 -o addr show dev oaitun_ue1 | awk '{print $4}' | cut -d/ -f1)" >&2 || true
  ip route get "$EDGE_IP" from "$(ip -4 -o addr show dev oaitun_ue2 | awk '{print $4}' | cut -d/ -f1)" >&2 || true
  docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E "$EDGE_CONTAINER_NAME|$CORE_AMF_CONTAINER|$CORE_SMF_CONTAINER|$CORE_UPF_CONTAINER" >&2 || true
  docker exec "$EDGE_CONTAINER_NAME" ip -4 -o addr show >&2 || true
  docker exec "$EDGE_CONTAINER_NAME" ip route >&2 || true
  docker exec "$EDGE_CONTAINER_NAME" ping -c 2 -W 2 "$UPF_N6_GATEWAY" >&2 || true
  docker logs --tail=80 "$CORE_SMF_CONTAINER" >&2 || true
  docker logs --tail=80 "$CORE_UPF_CONTAINER" >&2 || true
}

check_tunnel_ping() {
  local iface="$1"

  if run ping -I "$iface" -c 3 -W 2 "$EDGE_IP"; then
    return 0
  fi

  dump_reachability_debug
  return 1
}

check_tunnel_tcp() {
  local iface="$1"
  local bind_ip
  bind_ip="$(ip -4 -o addr show dev "$iface" | awk '{print $4}' | cut -d/ -f1)"
  if [[ -z "$bind_ip" ]]; then
    echo "[Recovery ERROR] Cannot determine IPv4 address for $iface" >&2
    return 1
  fi

  echo "[Recovery] Checking TCP reachability from $iface ($bind_ip) to $EDGE_IP:$CONNECTIVITY_TEST_PORT"
  cleanup_edge_iperf_port
  run docker exec -d "$EDGE_CONTAINER_NAME" sh -lc "iperf3 -s -1 -p '$CONNECTIVITY_TEST_PORT' >/tmp/orbit3c_recovery_iperf3_${CONNECTIVITY_TEST_PORT}.log 2>&1"
  sleep 1

  local bind_args=("-B" "$bind_ip")
  if iperf3 --help 2>&1 | grep -q -- "--bind-dev"; then
    bind_args+=("--bind-dev" "$iface")
  fi

  if run timeout "$CONNECTIVITY_TEST_TIMEOUT_SEC" iperf3 -c "$EDGE_IP" -p "$CONNECTIVITY_TEST_PORT" "${bind_args[@]}" -t 2 -O 1; then
    cleanup_edge_iperf_port
    return 0
  fi

  dump_reachability_debug
  cleanup_edge_iperf_port
  return 1
}

validate_upf_sessions() {
  if [[ "$VALIDATE_UPF_SESSIONS" != "1" ]]; then
    echo "[Recovery] Skipping UPF session validation because ORBIT3C_VALIDATE_UPF_SESSIONS=$VALIDATE_UPF_SESSIONS"
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[Recovery DRY_RUN] would validate UPF sessions in $CORE_UPF_CONTAINER"
    return 0
  fi

  local ue1_ip ue2_ip sessions start elapsed last_report
  ue1_ip="$(ip -4 -o addr show dev oaitun_ue1 | awk '{print $4}' | cut -d/ -f1)"
  ue2_ip="$(ip -4 -o addr show dev oaitun_ue2 | awk '{print $4}' | cut -d/ -f1)"

  echo "[Recovery] Validating UPF sessions for $ue1_ip and $ue2_ip"
  start="$(date +%s)"
  last_report=0
  while true; do
    sessions="$(docker exec "$CORE_UPF_CONTAINER" sh -lc "'$VPPCTL_PATH' show upf session" 2>&1 || true)"
    if [[ -n "$sessions" ]] && grep -Fq "$ue1_ip" <<<"$sessions" && grep -Fq "$ue2_ip" <<<"$sessions"; then
      printf '%s\n' "$sessions"
      return 0
    fi

    elapsed=$(( $(date +%s) - start ))
    if (( elapsed - last_report >= 10 )); then
      echo "[Recovery] Waiting for UPF sessions (${elapsed}s/${UPF_SESSION_WAIT_TIMEOUT_SEC}s)"
      last_report="$elapsed"
    fi
    if (( elapsed >= UPF_SESSION_WAIT_TIMEOUT_SEC )); then
      printf '%s\n' "$sessions"
      echo "[Recovery ERROR] UPF sessions are missing or do not include both UE tunnel IPs." >&2
      echo "[Recovery DEBUG] Recent SMF log:" >&2
      docker logs --tail=160 "$CORE_SMF_CONTAINER" >&2 || true
      echo "[Recovery DEBUG] Recent UPF log:" >&2
      docker logs --tail=160 "$CORE_UPF_CONTAINER" >&2 || true
      echo "[Recovery DEBUG] UPF errors:" >&2
      docker exec "$CORE_UPF_CONTAINER" sh -lc "'$VPPCTL_PATH' show error" >&2 || true
      return 1
    fi
    sleep 2
  done
}

check_tunnel_connectivity() {
  local iface="$1"

  case "$CONNECTIVITY_CHECK" in
    tcp)
      check_tunnel_tcp "$iface"
      ;;
    ping)
      check_tunnel_ping "$iface"
      ;;
    none)
      echo "[Recovery] Skipping $iface connectivity check because ORBIT3C_RECOVERY_CONNECTIVITY_CHECK=none"
      ;;
    *)
      echo "[Recovery ERROR] ORBIT3C_RECOVERY_CONNECTIVITY_CHECK must be tcp, ping, or none." >&2
      return 2
      ;;
  esac
}

echo "[Recovery] Starting OAI stack recovery"
echo "[Recovery] reason=${ORBIT3C_RECOVERY_REASON:-unspecified}"
echo "[Recovery] failed_run_id=${ORBIT3C_RECOVERY_RUN_ID:-unspecified}"

classify_recovery_reason
select_backend
echo "[Recovery CMD] sudo -n true"
if [[ "$DRY_RUN" != "1" ]] && ! sudo -n true; then
  echo "[Recovery ERROR] sudo cache is not available. Run 'sudo -v' before starting the runner, or leave ORBIT3C_SUDO_KEEPALIVE=1 enabled." >&2
  exit 1
fi
mkdir -p "$LOG_DIR"
require_core_running_or_recovering

echo "[Recovery] Stopping experiment, xApp, UE, gNB, and RIC processes"
run pkill -f factory_relay_oai_dev.py || true
run sudo -n pkill -f ue_forwarder_oai_dev.py || true
run pkill -f xapp_orbit3c_static_slice_ctrl || true
run pkill -f xapp_kpm_logger || true
run sudo -n pkill -f nr-uesoftmodem || true
run sudo -n pkill -f nr-softmodem || true
run pkill -f nearRT-RIC || true

if [[ "$BACKEND" == "tmux" ]]; then
  run tmux kill-session -t "$RIC_SESSION" || true
  run tmux kill-session -t "$GNB_SESSION" || true
  run tmux kill-session -t "$UE_SESSION" || true
fi

run sudo -n ip link del oaitun_ue1 || true
run sudo -n ip link del oaitun_ue2 || true
run sudo -n tc qdisc del dev oaitun_ue1 root || true
run sudo -n tc qdisc del dev oaitun_ue2 root || true

if [[ "$RECOVER_CORE" == "1" ]]; then
  echo "[Recovery] Recovering OAI core with mode=$CORE_RECOVERY_MODE scope=$CORE_RECOVERY_SCOPE"
  if [[ "$CORE_RECOVERY_MODE" == "restart" ]]; then
    if [[ "$CORE_RECOVERY_SCOPE" == "full" ]]; then
      run_bash "cd '$COMPOSE_DIR' && docker compose -f '$COMPOSE_FILE' restart '$CORE_MYSQL_SERVICE' '$CORE_NRF_SERVICE' '$CORE_UDR_SERVICE' '$CORE_UDM_SERVICE' '$CORE_AUSF_SERVICE' '$CORE_AMF_SERVICE' '$CORE_SMF_SERVICE' '$CORE_UPF_SERVICE' '$CORE_EXT_DN_SERVICE'"
    elif [[ "$CORE_RECOVERY_SCOPE" == "session" ]]; then
      run_bash "cd '$COMPOSE_DIR' && docker compose -f '$COMPOSE_FILE' restart '$CORE_AMF_SERVICE' '$CORE_SMF_SERVICE' '$CORE_UPF_SERVICE'"
    else
      echo "[Recovery ERROR] ORBIT3C_CORE_RECOVERY_SCOPE must be full or session." >&2
      exit 2
    fi
  elif [[ "$CORE_RECOVERY_MODE" == "recreate" ]]; then
    if [[ "$CORE_RECOVERY_SCOPE" == "full" ]]; then
      run docker rm -f "$CORE_MYSQL_CONTAINER" "$CORE_AUSF_CONTAINER" "$CORE_UDM_CONTAINER" "$CORE_UDR_CONTAINER" "$CORE_NRF_CONTAINER" "$CORE_AMF_CONTAINER" "$CORE_SMF_CONTAINER" "$CORE_UPF_CONTAINER" "$CORE_EXT_DN_CONTAINER" || true
      run_bash "cd '$COMPOSE_DIR' && docker compose -f '$COMPOSE_FILE' up -d --force-recreate '$CORE_MYSQL_SERVICE' '$CORE_NRF_SERVICE' '$CORE_UDR_SERVICE' '$CORE_UDM_SERVICE' '$CORE_AUSF_SERVICE' '$CORE_AMF_SERVICE' '$CORE_SMF_SERVICE' '$CORE_UPF_SERVICE' '$CORE_EXT_DN_SERVICE'"
    elif [[ "$CORE_RECOVERY_SCOPE" == "session" ]]; then
      run docker rm -f "$CORE_AMF_CONTAINER" "$CORE_SMF_CONTAINER" "$CORE_UPF_CONTAINER" || true
      run_bash "cd '$COMPOSE_DIR' && docker compose -f '$COMPOSE_FILE' up -d --force-recreate --no-deps '$CORE_AMF_SERVICE' '$CORE_SMF_SERVICE' '$CORE_UPF_SERVICE'"
    else
      echo "[Recovery ERROR] ORBIT3C_CORE_RECOVERY_SCOPE must be full or session." >&2
      exit 2
    fi
  else
    echo "[Recovery ERROR] ORBIT3C_CORE_RECOVERY_MODE must be recreate or restart." >&2
    exit 2
  fi
  if [[ "$DRY_RUN" != "1" ]]; then
    sleep "$POST_CORE_RESTART_SLEEP_SEC"
  fi
fi

echo "[Recovery] Starting nearRT-RIC as $RIC_SESSION"
start_session "$RIC_SESSION" "$LOG_DIR/nearRT-RIC.log" "cd '$FLEXRIC_DIR' && ./build/examples/ric/nearRT-RIC 2>&1 | tee '$LOG_DIR/nearRT-RIC.log'"
if [[ "$DRY_RUN" != "1" ]]; then
  sleep 5
fi

echo "[Recovery] Starting gNB as $GNB_SESSION"
start_session "$GNB_SESSION" "$LOG_DIR/gnb_dev_rfsim_e2.log" "cd '$OAI_RAN_DIR' && source oaienv && cd '$RUN_BUILD_DIR' && $GNB_CMD"
wait_for_log_pattern "$LOG_DIR/gnb_dev_rfsim_e2.log" "E2 SETUP|E2.*SETUP|RIC.*connected|E2AP" "gNB/E2"
wait_for_log_pattern_or_fail "$LOG_DIR/gnb_dev_rfsim_e2.log" "Received NGSetupResponse from AMF|reconnected to AMF|associated AMF" "gNB/AMF"

ue_ready=0
for ue_attempt in $(seq 1 "$UE_START_ATTEMPTS"); do
  echo "[Recovery] Starting two-UE nr-uesoftmodem as $UE_SESSION (attempt $ue_attempt/$UE_START_ATTEMPTS)"
  start_session "$UE_SESSION" "$LOG_DIR/ue_2ue_dev_rfsim_e2.log" "cd '$OAI_RAN_DIR' && source oaienv && cd '$RUN_BUILD_DIR' && $UE_CMD"

  if wait_for_iface oaitun_ue1 && wait_for_iface oaitun_ue2; then
    ue_ready=1
    break
  fi

  if [[ "$ue_attempt" != "$UE_START_ATTEMPTS" ]]; then
    echo "[Recovery WARN] UE started but required tunnel interfaces did not appear; restarting UE softmodem."
    run sudo -n pkill -f nr-uesoftmodem || true
    run sudo -n ip link del oaitun_ue1 || true
    run sudo -n ip link del oaitun_ue2 || true
    sleep 8
  fi
done

if [[ "$ue_ready" != "1" ]]; then
  echo "[Recovery ERROR] UE tunnel interfaces did not become ready after $UE_START_ATTEMPTS attempt(s)." >&2
  exit 1
fi

ensure_edge_return_route
validate_upf_sessions

echo "[Recovery] Checking edge reachability from both tunnels with $CONNECTIVITY_CHECK"
check_tunnel_connectivity oaitun_ue1
check_tunnel_connectivity oaitun_ue2

echo "[Recovery] OAI stack recovery completed"
echo "[Recovery] Note: backend=$BACKEND does not reuse your old terminal panes."
echo "[Recovery] Follow restarted logs with:"
echo "  tail -f '$LOG_DIR/nearRT-RIC.log'"
echo "  tail -f '$LOG_DIR/gnb_dev_rfsim_e2.log'"
echo "  tail -f '$LOG_DIR/ue_2ue_dev_rfsim_e2.log'"
