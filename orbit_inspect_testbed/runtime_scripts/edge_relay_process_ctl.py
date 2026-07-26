#!/usr/bin/env python3
"""Small /proc-based helper for managing edge_relay_oai_dev.py in edge-dev.

The edge-dev container used for the ORBIT-Inspect demo may not include procps
utilities such as pgrep/pkill. Keep this helper dependency-free so the live
launcher can still restart and inspect the edge relay reliably.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path


TARGET = "edge_relay_oai_dev.py"


def iter_edge_pids() -> list[int]:
    pids: list[int] = []
    self_pid = os.getpid()
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        if pid == self_pid:
            continue
        try:
            raw_cmdline = (proc_dir / "cmdline").read_bytes()
        except OSError:
            continue
        cmdline = raw_cmdline.replace(b"\0", b" ").decode("utf-8", "replace").strip()
        if TARGET in cmdline:
            pids.append(pid)
    return sorted(set(pids))


def describe_pid(pid: int) -> str:
    try:
        raw_cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return f"{pid} <exited>"
    cmdline = raw_cmdline.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    return f"{pid} {cmdline}"


def print_env() -> int:
    pids = iter_edge_pids()
    if not pids:
        return 1
    try:
        raw_env = Path(f"/proc/{pids[0]}/environ").read_bytes()
    except OSError:
        return 1
    sys.stdout.write(raw_env.replace(b"\0", b"\n").decode("utf-8", "replace"))
    return 0


def stop(timeout_sec: float) -> int:
    pids = iter_edge_pids()
    if not pids:
        print("no edge relay process found")
        return 0

    print("stopping edge relay process(es): " + ", ".join(str(pid) for pid in pids))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not iter_edge_pids():
            print("edge relay stopped")
            return 0
        time.sleep(0.2)

    survivors = iter_edge_pids()
    if survivors:
        print("force killing edge relay process(es): " + ", ".join(str(pid) for pid in survivors))
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    time.sleep(0.2)
    remaining = iter_edge_pids()
    if remaining:
        print("edge relay process(es) still running: " + ", ".join(str(pid) for pid in remaining), file=sys.stderr)
        return 1
    print("edge relay stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["env", "list", "stop"])
    parser.add_argument("--timeout-sec", type=float, default=3.0)
    args = parser.parse_args()

    if args.command == "env":
        return print_env()
    if args.command == "list":
        pids = iter_edge_pids()
        for pid in pids:
            print(describe_pid(pid))
        return 0 if pids else 1
    if args.command == "stop":
        return stop(args.timeout_sec)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
