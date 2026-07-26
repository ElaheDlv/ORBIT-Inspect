#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! sudo -n true; then
  echo "[ORBIT-Inspect ERROR] sudo cache is not available."
  echo "Run sudo -v first, then rerun this script."
  exit 1
fi

export ORBIT3C_RECOVERY_BACKEND="${ORBIT3C_RECOVERY_BACKEND:-nohup}"

if [ -z "${ORBIT3C_CONFIG_DIR:-}" ]; then
  export ORBIT3C_CONFIG_DIR="/home/elahe/user/ORBIT3C_OAI_DEV/configs"
fi

exec automated_experiments/scripts/recover_oai_stack.sh
