#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

RESULTS_DIR="${ORBIT_INSPECT_RESULTS_DIR:-$PWD/orbit_inspect_testbed/live_results/latest}"
ROS_SETUP="${ORBIT3C_ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
ROS_LOG_DIR="${ORBIT3C_ROS_LOG_DIR:-$RESULTS_DIR/logs/ros}"

mkdir -p "${RESULTS_DIR}"
mkdir -p "${ROS_LOG_DIR}"

if [ -z "${LATENCY_CSV_PATH:-}" ]; then
  SESSION_TS="${ORBIT_INSPECT_SESSION_TS:-$(date +%Y%m%d_%H%M%S)}"
  export LATENCY_CSV_PATH="$RESULTS_DIR/factory_latency_${SESSION_TS}.csv"
  if [ -e "$RESULTS_DIR/factory_latency.csv" ] && [ ! -L "$RESULTS_DIR/factory_latency.csv" ]; then
    mv "$RESULTS_DIR/factory_latency.csv" "$RESULTS_DIR/factory_latency_legacy_$(date +%Y%m%d_%H%M%S).csv"
  fi
  ln -sfn "$(basename "$LATENCY_CSV_PATH")" "$RESULTS_DIR/factory_latency.csv"
else
  export LATENCY_CSV_PATH
fi
export ROI_DEBUG_DIR="${ROI_DEBUG_DIR:-$RESULTS_DIR/factory_roi_debug}"
export ROS_LOG_DIR

if [ -f "$ROS_SETUP" ]; then
  # shellcheck disable=SC1090
  set +u
  source "$ROS_SETUP"
  set -u
fi

exec "${ORBIT3C_ROS_PYTHON:-/usr/bin/python3}" orbit_inspect_testbed/runtime_scripts/factory_relay_orbit_inspect_live.py
