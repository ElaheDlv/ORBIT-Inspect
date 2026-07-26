#!/usr/bin/env bash
set -u

cd "$(dirname "$0")/.."

EDGE_IP="${ORBIT3C_EDGE_IP:-192.168.83.150}"
EDGE_PORT="${ORBIT3C_EDGE_PORT:-9100}"
UE_BIND_INTERFACE="${ORBIT3C_UE_BIND_INTERFACE:-oaitun_ue1}"
SOCKET_TIMEOUT_SEC="${SOCKET_TIMEOUT_SEC:-120.0}"

echo "[ORBIT-Inspect UE-FWD] Supervisor started at $(date -Is)"
echo "[ORBIT-Inspect UE-FWD] edge=$EDGE_IP:$EDGE_PORT iface=$UE_BIND_INTERFACE timeout=$SOCKET_TIMEOUT_SEC"

if [ "$(id -u)" -ne 0 ]; then
  if sudo -n true >/dev/null 2>&1; then
    exec sudo -n env \
      EDGE_IP="$EDGE_IP" \
      EDGE_PORT="$EDGE_PORT" \
      UE_BIND_INTERFACE="$UE_BIND_INTERFACE" \
      SOCKET_TIMEOUT_SEC="$SOCKET_TIMEOUT_SEC" \
      "$0"
  fi
  echo "[ORBIT-Inspect UE-FWD ERROR] sudo cache is not available in this detached context."
  echo "[ORBIT-Inspect UE-FWD ERROR] Start through start_ue_forwarder_live.sh after running sudo -v."
  exit 1
fi

while true; do
  echo "[ORBIT-Inspect UE-FWD] Launching ue_forwarder_oai_dev.py at $(date -Is)"
  env \
    PYTHONUNBUFFERED=1 \
    EDGE_IP="$EDGE_IP" \
    EDGE_PORT="$EDGE_PORT" \
    UE_BIND_INTERFACE="$UE_BIND_INTERFACE" \
    SOCKET_TIMEOUT_SEC="$SOCKET_TIMEOUT_SEC" \
    python3 ue_forwarder_oai_dev.py
  status=$?
  echo "[ORBIT-Inspect UE-FWD] ue_forwarder_oai_dev.py exited with status $status at $(date -Is)"
  sleep 1
done
