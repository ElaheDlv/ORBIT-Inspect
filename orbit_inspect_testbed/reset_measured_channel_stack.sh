#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

MEASURED_CONFIG_DIR="${ORBIT3C_MEASURED_CONFIG_DIR:-/home/elahe/user/ORBIT3C_OAI_DEV/configs}"

if [ ! -f "$MEASURED_CONFIG_DIR/gnb_orbit3c_dev_rfsim_e2.conf" ]; then
  echo "[ORBIT-Inspect ERROR] Measured/default gNB config not found in $MEASURED_CONFIG_DIR"
  exit 1
fi

if [ ! -f "$MEASURED_CONFIG_DIR/ue_orbit3c_dev_rfsim_2ue.conf" ]; then
  echo "[ORBIT-Inspect ERROR] Measured/default 2-UE config not found in $MEASURED_CONFIG_DIR"
  exit 1
fi

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

if config_enables_chanmod "$MEASURED_CONFIG_DIR/gnb_orbit3c_dev_rfsim_e2.conf"; then
  echo "[ORBIT-Inspect ERROR] Refusing to use $MEASURED_CONFIG_DIR because gNB config enables chanmod."
  echo "Set ORBIT3C_MEASURED_CONFIG_DIR to the measured/default config directory."
  exit 1
fi

if config_enables_chanmod "$MEASURED_CONFIG_DIR/ue_orbit3c_dev_rfsim_2ue.conf"; then
  echo "[ORBIT-Inspect ERROR] Refusing to use $MEASURED_CONFIG_DIR because UE config enables chanmod."
  echo "Set ORBIT3C_MEASURED_CONFIG_DIR to the measured/default config directory."
  exit 1
fi

if ! sudo -n true; then
  echo "[ORBIT-Inspect ERROR] sudo cache is not available."
  echo "Run sudo -v first, then rerun this script."
  exit 1
fi

export ORBIT3C_CONFIG_DIR="$MEASURED_CONFIG_DIR"
export ORBIT3C_RECOVER_CORE="${ORBIT3C_RECOVER_CORE:-1}"
export ORBIT3C_RECOVERY_REASON="${ORBIT3C_RECOVERY_REASON:-orbit_inspect_measured_channel_reset}"
export ORBIT3C_RECOVERY_BACKEND="${ORBIT3C_RECOVERY_BACKEND:-nohup}"

if [ "${ORBIT3C_ALLOW_CUSTOM_RADIO_CMD:-0}" != "1" ]; then
  unset ORBIT3C_GNB_CMD
  unset ORBIT3C_UE_CMD
fi

echo "[ORBIT-Inspect] Resetting radio stack to measured/default RFsim config:"
echo "[ORBIT-Inspect]   ORBIT3C_CONFIG_DIR=$ORBIT3C_CONFIG_DIR"
echo "[ORBIT-Inspect]   RFsim chanmod disabled by config check"

exec automated_experiments/scripts/recover_oai_stack.sh
