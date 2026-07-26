#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path


DEFAULT_OUTPUT = Path(__file__).resolve().parent / "live_results" / "latest" / "live_rntis.json"
DEFAULT_RUN_DIR = Path(__file__).resolve().parent / "live_results" / "latest" / "rnti_probe"
RNTI_RE = re.compile(r"(?:0x)?([0-9a-fA-F]{1,4})")


def run_capture(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=timeout)


def format_rnti(value: int) -> str:
    return f"0x{value:04x}"


def parse_rnti(value: str) -> int | None:
    match = RNTI_RE.search(str(value))
    return int(match.group(1), 16) if match else None


def get_iface_ipv4(iface: str) -> str:
    out = run_capture(["ip", "-4", "-o", "addr", "show", "dev", iface])
    if out.returncode != 0:
        raise RuntimeError(f"cannot read IPv4 address for {iface}: {out.stderr.strip()}")
    match = re.search(r"\binet\s+([0-9.]+)/", out.stdout)
    if not match:
        raise RuntimeError(f"no IPv4 address found on {iface}: {out.stdout.strip()}")
    return match.group(1)


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def read_from_offset(path: Path, offset: int) -> str:
    with path.open("rb") as handle:
        handle.seek(offset)
        return handle.read().decode("utf-8", errors="replace")


def parse_grants(text: str) -> list[dict[str, int]]:
    text = text.strip()
    if not text:
        return []
    header = (
        "host_time_us,frame,slot,sched_frame,sched_slot,rnti,slice_id,"
        "rbStart,rbSize,startSymbolIndex,nrOfSymbols,num_dmrs_symb,"
        "N_PRB_DMRS,mcs_table,mcs,Qm,R,nrOfLayers,TBS_bytes,harq_round\n"
    )
    rows = []
    for row in csv.DictReader(io.StringIO(header + text)):
        rnti = parse_rnti(row.get("rnti", ""))
        if rnti is None:
            continue
        try:
            rows.append({
                "rnti": rnti,
                "tbs_bytes": int(float(row.get("TBS_bytes", 0) or 0)),
                "harq_round": int(float(row.get("harq_round", 0) or 0)),
            })
        except ValueError:
            continue
    return rows


def summarize_grants(text: str) -> dict:
    totals: dict[int, int] = defaultdict(int)
    newtx_totals: dict[int, int] = defaultdict(int)
    grants: dict[int, int] = defaultdict(int)
    for row in parse_grants(text):
        rnti = row["rnti"]
        totals[rnti] += row["tbs_bytes"]
        grants[rnti] += 1
        if row["harq_round"] == 0:
            newtx_totals[rnti] += row["tbs_bytes"]
    scores = newtx_totals if newtx_totals else totals
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return {"totals": totals, "newtx_totals": newtx_totals, "grants": grants, "ranked": ranked}


def cleanup_edge_port(container: str, port: int) -> None:
    cmd = (
        f"if command -v fuser >/dev/null 2>&1; then fuser -k {port}/tcp || true; fi; "
        "if command -v ss >/dev/null 2>&1; then "
        f"for pid in $(ss -ltnp 2>/dev/null | awk '$4 ~ /:{port}$/ {{print $NF}}' "
        "| sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p' | sort -u); do "
        "kill -TERM \"$pid\" 2>/dev/null || true; done; fi"
    )
    subprocess.run(["docker", "exec", container, "sh", "-lc", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def wait_for_edge_server(container: str, port: int, timeout_sec: float = 5.0) -> bool:
    deadline = time.time() + timeout_sec
    cmd = f"ss -ltn 2>/dev/null | grep -q ':{port} '"
    while time.time() < deadline:
        out = subprocess.run(["docker", "exec", container, "sh", "-lc", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if out.returncode == 0:
            return True
        time.sleep(0.2)
    return False


def probe_iface(args: argparse.Namespace, iface: str, port: int) -> tuple[int, dict]:
    bind_ip = get_iface_ipv4(iface)
    server_log = f"/tmp/orbit_inspect_rnti_probe_{port}_{iface}.log"
    cleanup_edge_port(args.edge_container, port)
    subprocess.run(
        ["docker", "exec", "-d", args.edge_container, "sh", "-lc", f"iperf3 -s -1 -p {port} >'{server_log}' 2>&1"],
        check=False,
    )
    if not wait_for_edge_server(args.edge_container, port):
        server_out = run_capture(["docker", "exec", args.edge_container, "sh", "-lc", f"cat '{server_log}' 2>/dev/null || true"])
        raise RuntimeError(f"iperf3 server did not start in {args.edge_container} on port {port}:\n{server_out.stdout}")

    offset = file_size(args.grant_log)
    cmd = [
        "timeout",
        str(args.timeout),
        "iperf3",
        "-c",
        args.edge_ip,
        "-p",
        str(port),
        "-B",
        bind_ip,
        "--bind-dev",
        iface,
        "-t",
        str(args.duration),
        "-O",
        "1",
    ]
    print(f"[RNTIProbe] Probing {iface} ({bind_ip}) on port {port}")
    client = run_capture(cmd, timeout=args.timeout + 5)
    time.sleep(0.5)
    server_out = run_capture(["docker", "exec", args.edge_container, "sh", "-lc", f"cat '{server_log}' 2>/dev/null || true"])
    grant_text = read_from_offset(args.grant_log, offset)
    summary = summarize_grants(grant_text)
    ranked = summary["ranked"]

    snapshot = {
        "iface": iface,
        "bind_ip": bind_ip,
        "client_cmd": cmd,
        "client_returncode": client.returncode,
        "client_stdout": client.stdout,
        "client_stderr": client.stderr,
        "server_stdout": server_out.stdout,
        "grant_offset": offset,
        "grant_bytes_read": len(grant_text.encode("utf-8", errors="replace")),
        "newtx_totals_by_rnti": {format_rnti(k): v for k, v in sorted(summary["newtx_totals"].items())},
        "totals_by_rnti": {format_rnti(k): v for k, v in sorted(summary["totals"].items())},
        "grants_by_rnti": {format_rnti(k): v for k, v in sorted(summary["grants"].items())},
    }

    args.run_dir.mkdir(parents=True, exist_ok=True)
    (args.run_dir / f"rnti_probe_{iface}.json").write_text(json.dumps(snapshot, indent=2) + "\n")

    if client.returncode != 0:
        raise RuntimeError(f"iperf3 probe failed for {iface}; see {args.run_dir / f'rnti_probe_{iface}.json'}")
    if not ranked:
        raise RuntimeError(f"probe for {iface} produced no ULSCH grants; see {args.run_dir / f'rnti_probe_{iface}.json'}")

    best_rnti, best_bytes = ranked[0]
    second_bytes = ranked[1][1] if len(ranked) > 1 else 0
    if best_bytes < args.min_tbs_bytes:
        raise RuntimeError(f"probe for {iface} is too weak: best={best_bytes}, minimum={args.min_tbs_bytes}")
    if second_bytes > 0 and best_bytes < second_bytes * args.dominance_ratio:
        raise RuntimeError(f"probe for {iface} is ambiguous: best={best_bytes}, second={second_bytes}, ratio={args.dominance_ratio}")

    print(f"[RNTIProbe] {iface} -> {format_rnti(best_rnti)} ({best_bytes} new-TX bytes)")
    return best_rnti, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatically discover live ORBIT-Inspect industrial/background RNTIs.")
    parser.add_argument("--industrial-iface", default="oaitun_ue1")
    parser.add_argument("--background-iface", default="oaitun_ue2")
    parser.add_argument("--edge-container", default="edge-dev")
    parser.add_argument("--edge-ip", default="192.168.83.150")
    parser.add_argument("--port", type=int, default=5210)
    parser.add_argument("--duration", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--min-tbs-bytes", type=int, default=1000)
    parser.add_argument("--dominance-ratio", type=float, default=1.5)
    parser.add_argument("--grant-log", type=Path, default=Path("/home/elahe/user/ORBIT3C_OAI_DEV/logs/ulsch_grants.csv"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    args = parser.parse_args()

    if args.industrial_iface == args.background_iface:
        raise SystemExit("industrial and background interfaces must be different")
    if not args.grant_log.exists():
        raise SystemExit(f"ULSCH grant log does not exist: {args.grant_log}")

    industrial_rnti, industrial_probe = probe_iface(args, args.industrial_iface, args.port)
    background_rnti, background_probe = probe_iface(args, args.background_iface, args.port + 1)
    if industrial_rnti == background_rnti:
        raise SystemExit(f"both interfaces mapped to {format_rnti(industrial_rnti)}; refusing to write live RNTIs")

    payload = {
        "source": "orbit_inspect_tunnel_ulsch_probe",
        "industrial_iface": args.industrial_iface,
        "background_iface": args.background_iface,
        "industrial_rnti": format_rnti(industrial_rnti),
        "background_rnti": format_rnti(background_rnti),
        "iface_to_rnti": {
            args.industrial_iface: format_rnti(industrial_rnti),
            args.background_iface: format_rnti(background_rnti),
        },
        "created_at": time.time(),
        "industrial_probe": industrial_probe,
        "background_probe": background_probe,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[RNTIProbe] Wrote {args.output}")
    print(f"[RNTIProbe] industrial={payload['industrial_rnti']} background={payload['background_rnti']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
