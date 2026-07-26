#!/usr/bin/env bash
set -euo pipefail

echo "[Check] ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-unset}"
echo "[Check] RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-unset}"

if ! command -v ros2 >/dev/null 2>&1; then
  echo "[ERROR] ros2 command not found. Source the same ROS2 environment used by Isaac." >&2
  exit 1
fi

echo "[Check] ORBIT topics:"
ros2 topic list | grep orbit_inspect || true

echo "[Check] /orbit_inspect/control:"
ros2 topic info /orbit_inspect/control || true

echo "[Check] /orbit_inspect/control_ack:"
ros2 topic info /orbit_inspect/control_ack || true
