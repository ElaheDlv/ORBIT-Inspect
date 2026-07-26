#!/usr/bin/env python3
"""Local live-control backend for the ORBIT-Inspect dashboard.

Default behavior is dry-run for system-level actuators. Set
ORBIT_LIVE_APPLY=1 only on the testbed machine after Isaac, OAI/FlexRIC, and
the edge container are already running.
"""

from __future__ import annotations

import argparse
import csv
import errno
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
CONTROL_TOPIC = os.environ.get("ORBIT_CONTROL_TOPIC", "/orbit_inspect/control")
CONTROL_ACK_TOPIC = os.environ.get("ORBIT_CONTROL_ACK_TOPIC", "/orbit_inspect/control_ack")
ISAAC_STATUS_TOPIC = os.environ.get("ORBIT_ISAAC_STATUS_TOPIC", "/orbit_inspect/isaac_status")
LIVE_LATENCY_CSV = os.environ.get("ORBIT_LIVE_LATENCY_CSV", "")
LIVE_EXPECTED_PARTS = int(os.environ.get("ORBIT_LIVE_EXPECTED_PARTS", "0"))
EDGE_CONTAINER = os.environ.get("ORBIT3C_EDGE_CONTAINER_NAME", "edge-dev")
SLICE_XAPP = Path(
    os.environ.get(
        "ORBIT3C_SLICE_XAPP",
        "/home/elahe/user/ORBIT3C_OAI_DEV/oai_ran/openairinterface5g/openair2/E2AP/flexric/build/examples/xApp/c/slice/xapp_orbit3c_static_slice_ctrl",
    )
)
FLEXRIC_DIR = Path(
    os.environ.get(
        "ORBIT3C_FLEXRIC_DIR",
        "/home/elahe/user/ORBIT3C_OAI_DEV/oai_ran/openairinterface5g/openair2/E2AP/flexric",
    )
)
STATE_PATH = BASE_DIR / "orbit_inspect_testbed" / "live_state.json"
TESTBED_DIR = BASE_DIR / "orbit_inspect_testbed"
LIVE_RESULTS_DIR = Path(
    os.environ.get(
        "ORBIT_INSPECT_RESULTS_DIR",
        str(TESTBED_DIR / "live_results" / "latest"),
    )
)
LIVE_CONFIG_PATH = Path(os.environ.get("ORBIT_INSPECT_LIVE_CONFIG", str(LIVE_RESULTS_DIR / "current_live_config.json")))
LIVE_RNTI_PATH = Path(os.environ.get("ORBIT3C_LIVE_RNTI_FILE", str(LIVE_RESULTS_DIR / "live_rntis.json")))
SLICE_LOG_PATH = Path(os.environ.get("ORBIT3C_LIVE_SLICE_LOG", str(LIVE_RESULTS_DIR / "logs" / "slice_xapp_stdout.txt")))
SLICE_POLICY_PATH = Path(os.environ.get("ORBIT3C_SLICE_POLICY_FILE", str(LIVE_RESULTS_DIR / "orbit3c_slice_policy.json")))
BACKGROUND_IPERF_MBIT = os.environ.get("ORBIT3C_BG_IPERF_MBIT", "30")
APP_STACK_SCRIPT = TESTBED_DIR / "start_live_application_stack.sh"
RUNNER_STYLE_APP_RESTART = os.environ.get("ORBIT_INSPECT_RUNNER_STYLE_RESTART", "1").lower() not in {"0", "false", "no"}
SCRIPT_DIR = BASE_DIR / "automated_experiments" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

RESOLUTION_RE = re.compile(r"^(?P<width>\d{3,4})x(?P<height>\d{2,4})$")


@dataclass
class LiveState:
    speed: float = 1.0
    resolution: str = "640x360"
    prb: int = 35
    cpu: float = 4.0
    apply_real: bool = False
    last_update: float = field(default_factory=time.time)
    run_id: str = ""
    latency_csv_path: str = ""
    run_status: str = "idle"
    online_measurement: dict[str, Any] = field(default_factory=dict)
    actuator_results: list[dict[str, Any]] = field(default_factory=list)
    isaac_status: dict[str, Any] = field(default_factory=dict)


STATE = LiveState(apply_real=os.environ.get("ORBIT_LIVE_APPLY", "0") == "1")
SLICE_PROC: subprocess.Popen[str] | None = None


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    if "speed" in payload:
        speed = float(payload["speed"])
        if not 0.1 <= speed <= 3.0:
            raise ValueError("speed must be in [0.1, 3.0] m/s")
        clean["speed"] = speed

    if "resolution" in payload:
        resolution = str(payload["resolution"]).lower()
        match = RESOLUTION_RE.match(resolution)
        if not match:
            raise ValueError("resolution must look like WIDTHxHEIGHT")
        width = int(match.group("width"))
        height = int(match.group("height"))
        if not 160 <= width <= 1920 or not 90 <= height <= 1080:
            raise ValueError("resolution must be between 160x90 and 1920x1080")
        clean["resolution"] = f"{width}x{height}"

    if "prb" in payload:
        prb = int(round(float(payload["prb"])))
        if not 1 <= prb <= 100:
            raise ValueError("UL PRB percentage must be in [1, 100]")
        clean["prb"] = prb

    if "cpu" in payload:
        cpu = float(payload["cpu"])
        if not 0.25 <= cpu <= 16.0:
            raise ValueError("CPU allocation must be in [0.25, 16.0]")
        clean["cpu"] = cpu

    if any(key in payload for key in ("channel_model", "path_loss_dB", "noise_power_dB")):
        raise ValueError("channel controls are disabled for the ORBIT-Inspect 1056-measurement demo path")

    if payload.get("start_run"):
        clean["start_run"] = True

    if not clean:
        raise ValueError("payload did not include any supported control fields")
    return clean


def command_result(name: str, status: str, detail: str = "", command: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "command": command or [],
        "timestamp": time.time(),
    }


def publish_ros_control(clean: dict[str, Any]) -> dict[str, Any]:
    message = json.dumps(clean)
    code = (
        "import json, os, sys, time\n"
        "import rclpy\n"
        "from std_msgs.msg import String\n"
        "acks = []\n"
        "def on_ack(msg):\n"
        "    try:\n"
        "        acks.append(json.loads(msg.data))\n"
        "    except Exception as exc:\n"
        "        acks.append({'component': 'unknown', 'status': 'parse_error', 'error': str(exc)})\n"
        "rclpy.init()\n"
        "node = rclpy.create_node('orbit_inspect_live_backend_once')\n"
        f"pub = node.create_publisher(String, {CONTROL_TOPIC!r}, 10)\n"
        f"sub = node.create_subscription(String, {CONTROL_ACK_TOPIC!r}, on_ack, 10)\n"
        "msg = String()\n"
        f"msg.data = {message!r}\n"
        "discovery_deadline = time.time() + 4.0\n"
        "max_subscribers = 0\n"
        "max_ack_publishers = 0\n"
        "while time.time() < discovery_deadline:\n"
        "    rclpy.spin_once(node, timeout_sec=0.05)\n"
        f"    max_subscribers = max(max_subscribers, node.count_subscribers({CONTROL_TOPIC!r}))\n"
        f"    max_ack_publishers = max(max_ack_publishers, node.count_publishers({CONTROL_ACK_TOPIC!r}))\n"
        "    if max_subscribers > 0:\n"
        "        break\n"
        "ack_deadline = time.time() + 4.0\n"
        "last_publish = 0.0\n"
        "while time.time() < ack_deadline:\n"
        "    now = time.time()\n"
        "    if now - last_publish >= 0.25:\n"
        "        pub.publish(msg)\n"
        "        last_publish = now\n"
        "    rclpy.spin_once(node, timeout_sec=0.05)\n"
        f"    max_ack_publishers = max(max_ack_publishers, node.count_publishers({CONTROL_ACK_TOPIC!r}))\n"
        "    components = {ack.get('component') for ack in acks}\n"
        "    if 'isaac' in components and 'factory_relay' in components:\n"
        "        break\n"
        "print(json.dumps({\n"
        "    'published': json.loads(msg.data),\n"
        "    'acks': acks,\n"
        "    'control_subscribers': max_subscribers,\n"
        "    'ack_publishers': max_ack_publishers,\n"
        "    'ros_domain_id': os.environ.get('ROS_DOMAIN_ID', ''),\n"
        "    'rmw_implementation': os.environ.get('RMW_IMPLEMENTATION', ''),\n"
        "}))\n"
        "node.destroy_node()\n"
        "rclpy.shutdown()\n"
    )
    result = subprocess.run(
        [os.environ.get("ORBIT3C_ROS_PYTHON", "/usr/bin/python3"), "-c", code],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return command_result("ros_control", "error", result.stderr.strip() or result.stdout.strip())
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:
        payload = {"published": clean, "acks": [], "stdout": result.stdout.strip()}
    acks = payload.get("acks", [])
    subscribers = int(payload.get("control_subscribers", 0) or 0)
    ack_publishers = int(payload.get("ack_publishers", 0) or 0)
    components = {ack.get("component") for ack in acks}
    if {"isaac", "factory_relay"}.issubset(components):
        status = "acknowledged"
    elif acks:
        status = "partial_ack"
    elif subscribers <= 0:
        status = "no_subscribers"
    else:
        status = "published_no_ack"
    return {
        **command_result(
            "ros_control",
            status,
            (
                f"published {message}; subscribers={subscribers}; "
                f"ack_publishers={ack_publishers}; acks={len(acks)}"
            ),
        ),
        "acks": acks,
        "control_subscribers": subscribers,
        "ack_publishers": ack_publishers,
        "ros_domain_id": payload.get("ros_domain_id", ""),
        "rmw_implementation": payload.get("rmw_implementation", ""),
    }


def poll_isaac_status() -> dict[str, Any]:
    code = (
        "import json, os, time\n"
        "import rclpy\n"
        "from std_msgs.msg import String\n"
        "messages = []\n"
        "def on_msg(msg):\n"
        "    try:\n"
        "        messages.append(json.loads(msg.data))\n"
        "    except Exception as exc:\n"
        "        messages.append({'component': 'isaac', 'phase': 'parse_error', 'error': str(exc)})\n"
        "rclpy.init()\n"
        "node = rclpy.create_node('orbit_inspect_live_backend_isaac_status_once')\n"
        f"sub = node.create_subscription(String, {ISAAC_STATUS_TOPIC!r}, on_msg, 10)\n"
        "deadline = time.time() + 0.35\n"
        "publishers = 0\n"
        "while time.time() < deadline:\n"
        f"    publishers = max(publishers, node.count_publishers({ISAAC_STATUS_TOPIC!r}))\n"
        "    rclpy.spin_once(node, timeout_sec=0.05)\n"
        "    if messages:\n"
        "        break\n"
        "print(json.dumps({'publishers': publishers, 'message': messages[-1] if messages else None}))\n"
        "node.destroy_node()\n"
        "rclpy.shutdown()\n"
    )
    result = subprocess.run(
        [os.environ.get("ORBIT3C_ROS_PYTHON", "/usr/bin/python3"), "-c", code],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return {
            "phase": "unavailable",
            "detail": result.stderr.strip() or result.stdout.strip(),
            "timestamp": time.time(),
        }
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:
        return {"phase": "unavailable", "detail": result.stdout.strip(), "timestamp": time.time()}
    message = payload.get("message")
    if isinstance(message, dict):
        message["status_age_sec"] = max(0.0, time.time() - float(message.get("timestamp", time.time())))
        message["publishers"] = int(payload.get("publishers", 0) or 0)
        return message
    return {
        "phase": "waiting_for_isaac_status",
        "publishers": int(payload.get("publishers", 0) or 0),
        "timestamp": time.time(),
    }


def apply_cpu(cpu: float, apply_real: bool) -> dict[str, Any]:
    command = ["docker", "update", "--cpus", str(cpu), EDGE_CONTAINER]
    if not apply_real:
        return command_result("edge_cpu", "dry_run", f"would set {EDGE_CONTAINER} to {cpu} CPUs", command)
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return command_result("edge_cpu", "error", result.stderr.strip() or result.stdout.strip(), command)
    inspect = subprocess.run(
        ["docker", "inspect", "--format", "{{.HostConfig.NanoCpus}}", EDGE_CONTAINER],
        text=True,
        capture_output=True,
        check=False,
    )
    detail = result.stdout.strip() or EDGE_CONTAINER
    if inspect.returncode == 0:
        try:
            nano_cpus = int(inspect.stdout.strip() or "0")
            if nano_cpus > 0:
                detail = f"{detail}; verified Docker CPU limit {nano_cpus / 1_000_000_000:g}"
            else:
                detail = f"{detail}; Docker CPU limit is unlimited"
        except ValueError:
            detail = f"{detail}; Docker inspect returned {inspect.stdout.strip()}"
    return command_result("edge_cpu", "applied", detail, command)


def edge_label(cpu: float) -> str:
    if float(cpu).is_integer():
        return f"edge_cpu{int(cpu)}"
    return f"edge_cpu{str(cpu).replace('.', 'p')}"


def edge_compute_capacity(cpu: float) -> float:
    return round(float(cpu) / 8.0, 3)


def restart_application_stack_runner_style(clean: dict[str, Any], apply_real: bool) -> dict[str, Any]:
    command = [str(APP_STACK_SCRIPT)]
    cpu = float(clean.get("cpu", STATE.cpu))
    speed = float(clean.get("speed", STATE.speed))
    if not apply_real:
        return command_result(
            "application_stack",
            "dry_run",
            "would restart edge/factory relays with experiment-runner-style per-run env",
            command,
        )
    if not APP_STACK_SCRIPT.exists():
        return command_result("application_stack", "error", f"missing {APP_STACK_SCRIPT}", command)

    env = os.environ.copy()
    env["ORBIT_INSPECT_RESTART_EDGE"] = "1"
    env["ORBIT_INSPECT_RESTART_FACTORY"] = "1"
    env["ORBIT_INSPECT_RUN_ID"] = STATE.run_id
    env["ORBIT_INSPECT_RUN_SPEED"] = str(speed)
    env["ORBIT_INSPECT_EDGE_CPUS"] = str(cpu)
    env["ORBIT_INSPECT_EDGE_LABEL"] = edge_label(cpu)
    env["ORBIT_INSPECT_EDGE_COMPUTE_CAPACITY"] = str(edge_compute_capacity(cpu))
    env["ORBIT_INSPECT_EDGE_RESOURCE_CONTROL"] = "docker_update_cpus"
    env["LATENCY_CSV_PATH"] = STATE.latency_csv_path
    env["RUN_SPEED"] = str(speed)
    env["EDGE_CPUS"] = str(cpu)
    env["EDGE_LABEL"] = edge_label(cpu)
    env["EDGE_COMPUTE_CAPACITY"] = str(edge_compute_capacity(cpu))
    env["EDGE_RESOURCE_CONTROL"] = "docker_update_cpus"
    env.setdefault("ORBIT3C_BG_IPERF_MBIT", BACKGROUND_IPERF_MBIT)
    env["ORBIT_INSPECT_START_BACKGROUND"] = "0"

    result = subprocess.run(
        command,
        cwd=BASE_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )
    detail = result.stdout.strip()
    if result.returncode != 0:
        return command_result(
            "application_stack",
            "error",
            result.stderr.strip() or detail or f"{APP_STACK_SCRIPT} failed",
            command,
        )
    return command_result(
        "application_stack",
        "restarted",
        f"runner-style edge/factory relays for {STATE.run_id}; speed={speed}; cpu={cpu}; {detail.splitlines()[-1] if detail else ''}",
        command,
    )


def normalize_rnti(value: Any) -> str:
    text = str(value).strip().lower()
    if not text:
        raise ValueError("empty RNTI")
    if text.startswith("0x"):
        number = int(text, 16)
    else:
        number = int(text, 16)
    if number <= 0 or number > 0xFFFF:
        raise ValueError(f"RNTI out of range: {value}")
    return f"0x{number:04x}"


def resolve_live_rntis(env: dict[str, str]) -> dict[str, str] | None:
    env_industrial = env.get("ORBIT3C_SLICE_RNTI")
    env_background = env.get("ORBIT3C_BG_RNTI")
    if env_industrial and env_background:
        return {
            "industrial_rnti": normalize_rnti(env_industrial),
            "background_rnti": normalize_rnti(env_background),
            "source": "environment",
        }

    if LIVE_RNTI_PATH.exists():
        try:
            data = json.loads(LIVE_RNTI_PATH.read_text())
            return {
                "industrial_rnti": normalize_rnti(data["industrial_rnti"]),
                "background_rnti": normalize_rnti(data["background_rnti"]),
                "source": str(LIVE_RNTI_PATH),
            }
        except Exception as exc:
            raise ValueError(f"invalid live RNTI file {LIVE_RNTI_PATH}: {exc}") from exc

    return None


def apply_slice(prb: int, apply_real: bool) -> dict[str, Any]:
    global SLICE_PROC
    env = os.environ.copy()
    env["ORBIT3C_SLICE_UL_PCT"] = str(prb)
    env.setdefault("ORBIT3C_SLICE_DL_PCT", "50")
    env.setdefault("ORBIT3C_SLICE_KEEPALIVE_SEC", "1800")
    env["ORBIT3C_SLICE_POLICY_FILE"] = str(SLICE_POLICY_PATH)
    command = [str(SLICE_XAPP)]
    if not apply_real:
        return command_result("ul_prb", "dry_run", f"would launch/update slice xApp at UL {prb}%", command)
    if not SLICE_XAPP.exists():
        return command_result("ul_prb", "error", f"slice xApp not found: {SLICE_XAPP}", command)

    rntis = resolve_live_rntis(env)
    if not rntis:
        return command_result(
            "ul_prb",
            "error",
            (
                "real PRB control requires current industrial/background RNTIs. "
                f"Set ORBIT3C_SLICE_RNTI and ORBIT3C_BG_RNTI, or create {LIVE_RNTI_PATH}"
            ),
            command,
        )
    env["ORBIT3C_SLICE_RNTI"] = rntis["industrial_rnti"]
    env["ORBIT3C_BG_RNTI"] = rntis["background_rnti"]

    if SLICE_PROC is not None and SLICE_PROC.poll() is None:
        SLICE_PROC.terminate()
        try:
            SLICE_PROC.wait(timeout=3)
        except subprocess.TimeoutExpired:
            SLICE_PROC.kill()

    SLICE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    slice_log = SLICE_LOG_PATH.open("a")
    SLICE_PROC = subprocess.Popen(
        command,
        cwd=FLEXRIC_DIR,
        env=env,
        stdout=slice_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return command_result(
        "ul_prb",
        "started",
        (
            f"started slice xApp with industrial UL PRB {prb}% "
            f"background UL PRB {max(0, 100 - prb)}% "
            f"background offered load {BACKGROUND_IPERF_MBIT} Mb/s "
            f"industrial={rntis['industrial_rnti']} background={rntis['background_rnti']} "
            f"policy={SLICE_POLICY_PATH}"
        ),
        command,
    )


def apply_live_control(clean: dict[str, Any], apply_real: bool) -> list[dict[str, Any]]:
    results = []
    if "cpu" in clean:
        results.append(apply_cpu(float(clean["cpu"]), apply_real))
    if RUNNER_STYLE_APP_RESTART and any(key in clean for key in ("speed", "resolution", "cpu", "start_run")):
        app_result = restart_application_stack_runner_style(clean, apply_real)
        results.append(app_result)
        if app_result["status"] == "error":
            return results
    if "prb" in clean:
        results.append(apply_slice(int(clean["prb"]), apply_real))
    if "speed" in clean or "resolution" in clean or "start_run" in clean:
        results.append(publish_ros_control(clean))
    return results


def safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


def row_float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key)
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sorted_nonempty(rows: list[dict[str, str]], key: str) -> list[str]:
    return sorted({row.get(key, "") for row in rows if row.get(key)})


def observed_measurement_fields(rows: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "observed_resolutions": sorted_nonempty(rows, "resolution"),
        "observed_sent_resolutions": sorted_nonempty(rows, "sent_resolution"),
        "observed_control_resolutions": sorted_nonempty(rows, "control_resolution"),
        "observed_control_speeds": sorted_nonempty(rows, "control_speed"),
        "observed_control_cpus": sorted_nonempty(rows, "control_cpu"),
        "observed_control_prbs": sorted_nonempty(rows, "control_prb"),
        "observed_run_ids": sorted_nonempty(rows, "control_run_id"),
        "observed_factory_pids": sorted_nonempty(rows, "factory_pid"),
        "observed_edge_cpus": sorted_nonempty(rows, "edge_cpus"),
        "observed_edge_compute_capacity": sorted_nonempty(rows, "edge_compute_capacity"),
    }


def numeric_matches(value: str | None, expected: float, tolerance: float = 0.001) -> bool:
    if value in {"", None}:
        return False
    try:
        return abs(float(value) - expected) <= tolerance
    except (TypeError, ValueError):
        return False


def resolution_matches(value: str | None, expected: str) -> bool:
    return bool(value) and value.strip().lower() == expected.strip().lower()


def row_matches_current_control(row: dict[str, str]) -> bool:
    if not resolution_matches(row.get("control_resolution"), STATE.resolution):
        return False
    sent_resolution = row.get("sent_resolution") or row.get("resolution")
    if sent_resolution and not resolution_matches(sent_resolution, STATE.resolution):
        return False
    if not numeric_matches(row.get("control_speed"), float(STATE.speed)):
        return False
    if not numeric_matches(row.get("control_prb"), float(STATE.prb)):
        return False
    if not numeric_matches(row.get("control_cpu"), float(STATE.cpu)):
        return False
    if row.get("control_run_id") != STATE.run_id:
        return False
    request_run_id = row.get("request_run_id", "")
    if request_run_id and request_run_id != STATE.run_id:
        return False
    return True


REPLAY_TEXT_KEYS = (
    "status",
    "failure_type",
    "decision",
    "roi_status",
    "frame_pick_mode",
    "obj_id",
    "part_name",
)
REPLAY_PATTERNS = (
    "replay",
    "partial",
    "no_fresh_image",
    "capture_no_fresh_image",
    "not_fully_visible",
    "dropped_factory_inflight_limit",
    "encode_failed",
    "factory_exception",
    "unexpected_result_type",
)


def row_text(row: dict[str, str], keys: tuple[str, ...]) -> str:
    return " ".join(str(row.get(key, "")).lower() for key in keys)


def is_replay_or_problem_row(row: dict[str, str]) -> bool:
    text = row_text(row, REPLAY_TEXT_KEYS)
    return any(pattern in text for pattern in REPLAY_PATTERNS)


def status_has(row: dict[str, str], *patterns: str) -> bool:
    status = str(row.get("status", "")).lower()
    return any(pattern in status for pattern in patterns)


def row_bool(row: dict[str, str], key: str) -> bool:
    value = row_float(row, key)
    if value is not None:
        return bool(value)
    return str(row.get(key, "")).strip().lower() in {"true", "yes"}


def read_online_measurement() -> dict[str, Any]:
    latency_csv = STATE.latency_csv_path or LIVE_LATENCY_CSV
    if not latency_csv:
        return {
            "status": "not_configured",
            "detail": "set ORBIT_LIVE_LATENCY_CSV to summarize online results",
        }
    path = Path(latency_csv).expanduser()
    if not path.exists():
        return {"status": "waiting_for_file", "path": str(path)}

    rows: list[dict[str, str]] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                ts = float(row.get("run_ts", "0") or 0.0)
            except ValueError:
                ts = 0.0
            if ts >= STATE.last_update:
                rows.append(row)

    if not rows:
        return {"status": "waiting_for_rows", "path": str(path), "rows": 0}

    candidate_count = len(rows)
    observed_candidates = observed_measurement_fields(rows)
    matching_rows = [row for row in rows if row_matches_current_control(row)]
    if not matching_rows:
        schema_has_live_control = any(row.get("control_run_id") for row in rows)
        return {
            "status": "waiting_for_matching_rows" if schema_has_live_control else "stale_measurement_schema",
            "path": str(path),
            "rows": 0,
            "candidate_rows": candidate_count,
            "expected_rows": LIVE_EXPECTED_PARTS,
            "expected_control": {
                "run_id": STATE.run_id,
                "speed": STATE.speed,
                "resolution": STATE.resolution,
                "prb": STATE.prb,
                "cpu": STATE.cpu,
            },
            "detail": (
                "Rows are arriving, but none match this Apply Live run."
                if schema_has_live_control
                else "Latency CSV rows do not include live control fields; restart the ORBIT-Inspect factory relay."
            ),
            **observed_candidates,
        }

    raw_matching_rows = matching_rows
    rate_rows = [row for row in raw_matching_rows if not is_replay_or_problem_row(row)]
    replay_removed = len(raw_matching_rows) - len(rate_rows)
    latency_rows = rate_rows

    if not rate_rows:
        return {
            "status": "collecting",
            "path": str(path),
            "rows": 0,
            "raw_matching_rows": len(raw_matching_rows),
            "candidate_rows": candidate_count,
            "expected_rows": LIVE_EXPECTED_PARTS,
            "replay_rows_removed": replay_removed,
            "detail": "Only replay/problem rows have arrived for this run so far.",
            **observed_measurement_fields(raw_matching_rows),
        }

    roundtrip = [
        value for row in latency_rows
        for value in [row_float(row, "factory_roundtrip_ms")]
        if value is not None
    ]
    inference = [
        value for row in latency_rows
        for value in [row_float(row, "inference_ms")]
        if value is not None
    ]
    correct_flags = [row_bool(row, "correct") for row in rate_rows]
    late_flags = [
        status_has(row, "late_for_sort", "late") or row_bool(row, "late_for_sort_edge")
        for row in rate_rows
    ]
    unknown_flags = [status_has(row, "unknown") for row in rate_rows]
    no_ack_flags = [status_has(row, "no_ack") for row in rate_rows]
    ok_flags = [status_has(row, "ok") for row in rate_rows]
    correct_on_time = []
    for index, row in enumerate(rate_rows):
        is_correct = correct_flags[index]
        on_time = not (late_flags[index] or unknown_flags[index] or no_ack_flags[index])
        if is_correct and on_time:
            correct_on_time.append(1)
    status = "finished" if LIVE_EXPECTED_PARTS and len(rate_rows) >= LIVE_EXPECTED_PARTS else "collecting"
    return {
        "status": status,
        "path": str(path),
        "rows": len(rate_rows),
        "raw_matching_rows": len(raw_matching_rows),
        "candidate_rows": candidate_count,
        "expected_rows": LIVE_EXPECTED_PARTS,
        "replay_rows_removed": replay_removed,
        "latency_rows": len(latency_rows),
        "correct_count": int(sum(correct_flags)),
        "late_count": int(sum(late_flags)),
        "unknown_count": int(sum(unknown_flags)),
        "no_ack_count": int(sum(no_ack_flags)),
        "ok_count": int(sum(ok_flags)),
        "correct_on_time_count": len(correct_on_time),
        "correct_on_time_denominator": len(rate_rows),
        "mean_roundtrip_ms": safe_mean(roundtrip),
        "p95_roundtrip_ms": percentile(roundtrip, 95),
        "mean_inference_ms": safe_mean(inference),
        "correct_rate": safe_mean([int(value) for value in correct_flags]),
        "late_rate": safe_mean([int(value) for value in late_flags]),
        "unknown_rate": safe_mean([int(value) for value in unknown_flags]),
        "no_ack_rate": safe_mean([int(value) for value in no_ack_flags]),
        "ok_rate": safe_mean([int(value) for value in ok_flags]),
        "correct_on_time_rate": len(correct_on_time) / len(rate_rows),
        **observed_measurement_fields(rate_rows),
    }


def isaac_reported_already_running(results: list[dict[str, Any]]) -> bool:
    saw_queued_for_current_run = False
    saw_already_running = False
    for result in results:
        if result.get("name") != "ros_control":
            continue
        for ack in result.get("acks", []):
            if ack.get("component") != "isaac":
                continue
            if ack.get("run_id") != STATE.run_id:
                continue
            if ack.get("run_request") == "queued":
                saw_queued_for_current_run = True
            elif ack.get("run_request") == "already_running":
                saw_already_running = True
    return saw_already_running and not saw_queued_for_current_run


def refresh_run_status() -> None:
    STATE.isaac_status = poll_isaac_status()
    if STATE.run_status in {"running", "collecting", "finished", "already_running"}:
        measurement = read_online_measurement()
        STATE.online_measurement = measurement
        if measurement.get("status") == "finished":
            STATE.run_status = "finished"
        elif measurement.get("rows", 0):
            STATE.run_status = "collecting"
        else:
            STATE.run_status = "running"


def save_state() -> None:
    STATE_PATH.write_text(json.dumps(asdict(STATE), indent=2))


def live_latency_csv_path(run_id: str) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    return LIVE_RESULTS_DIR / f"factory_latency_{timestamp}_{run_id}.csv"


def update_latest_latency_link(path: Path) -> None:
    latest = LIVE_RESULTS_DIR / "factory_latency.csv"
    LIVE_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if latest.exists() and not latest.is_symlink():
        backup = LIVE_RESULTS_DIR / f"factory_latency_legacy_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}.csv"
        latest.rename(backup)
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(path.name)


def write_live_config() -> dict[str, Any]:
    match = RESOLUTION_RE.match(STATE.resolution)
    if not match:
        raise ValueError(f"invalid live resolution in state: {STATE.resolution}")
    width = int(match.group("width"))
    height = int(match.group("height"))
    config = {
        "run_id": STATE.run_id,
        "run_speed": float(STATE.speed),
        "camera_width": width,
        "camera_height": height,
        "render_product_node": os.environ.get(
            "RENDER_PRODUCT_NODE",
            "/World/ActionGraph_01/isaac_create_render_product",
        ),
        "slice_ul_pct": int(STATE.prb),
        "slice_dl_pct": 50,
        "edge_cpus": float(STATE.cpu),
        "edge_resource_control": "docker_update_cpus",
        "background_iperf_mbit": BACKGROUND_IPERF_MBIT,
        "latency_csv_path": STATE.latency_csv_path,
        "run_dir": str(LIVE_RESULTS_DIR),
        "source": "orbit_inspect_testbed",
        "created_time": time.time(),
    }
    LIVE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIVE_CONFIG_PATH.write_text(json.dumps(config, indent=2))
    return config


class LiveHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, indent=2).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return
        except OSError as exc:
            if exc.errno in {errno.EPIPE, errno.ECONNRESET}:
                return
            raise

    def do_GET(self) -> None:
        if self.path == "/api/status":
            refresh_run_status()
            self.send_json(200, {"ok": True, "state": asdict(STATE)})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/control":
            self.send_json(404, {"ok": False, "error": "unknown endpoint"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            clean = validate_payload(payload)
            for key, value in clean.items():
                setattr(STATE, key, value)
            STATE.last_update = time.time()
            STATE.run_id = f"live_{int(STATE.last_update)}"
            latency_path = live_latency_csv_path(STATE.run_id)
            STATE.latency_csv_path = str(latency_path)
            update_latest_latency_link(latency_path)
            clean["run_id"] = STATE.run_id
            clean["config_path"] = str(LIVE_CONFIG_PATH)
            write_live_config()
            STATE.run_status = "running"
            STATE.online_measurement = read_online_measurement()
            STATE.actuator_results = apply_live_control(clean, STATE.apply_real)
            failed_actuator = next((item for item in STATE.actuator_results if item.get("status") == "error"), None)
            if failed_actuator:
                STATE.run_status = "error"
                STATE.online_measurement = {
                    "status": "actuator_error",
                    "detail": f"{failed_actuator.get('name')}: {failed_actuator.get('detail')}",
                    "rows": 0,
                }
            elif clean.get("start_run") and isaac_reported_already_running(STATE.actuator_results):
                STATE.run_status = "already_running"
                STATE.online_measurement = {
                    "status": "run_already_running",
                    "detail": "Isaac was already running; wait for it to finish, then click Apply Live for a clean online measurement.",
                    "rows": 0,
                }
            save_state()
            self.send_json(200, {"ok": True, "state": asdict(STATE)})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc), "state": asdict(STATE)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), LiveHandler)
    mode = "REAL APPLY" if STATE.apply_real else "dry-run system actuators"
    print(f"[LiveBackend] Serving http://{args.host}:{args.port}/orbit_inspect_testbed/index.html")
    print(f"[LiveBackend] Mode: {mode}")
    print(f"[LiveBackend] ROS control topic: {CONTROL_TOPIC}")
    server.serve_forever()


if __name__ == "__main__":
    main()
