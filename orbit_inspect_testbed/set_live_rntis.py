#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parent / "live_results" / "latest" / "live_rntis.json"


def normalize_rnti(value: str) -> str:
    text = value.strip().lower()
    match = re.fullmatch(r"(?:0x)?([0-9a-f]{1,4})", text)
    if not match:
        raise argparse.ArgumentTypeError(f"invalid RNTI: {value}")
    number = int(match.group(1), 16)
    if number <= 0:
        raise argparse.ArgumentTypeError("RNTI must be nonzero")
    return f"0x{number:04x}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Save the current live RNTI mapping for the ORBIT-Inspect dashboard.")
    parser.add_argument("--industrial", required=True, type=normalize_rnti, help="RNTI for the industrial/oaitun_ue1 flow, for example 0x70cb")
    parser.add_argument("--background", required=True, type=normalize_rnti, help="RNTI for the background/oaitun_ue2 flow, for example 0xe68f")
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()

    if args.industrial == args.background:
        raise SystemExit("industrial and background RNTIs must be different")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "industrial_rnti": args.industrial,
        "background_rnti": args.background,
        "industrial_iface": "oaitun_ue1",
        "background_iface": "oaitun_ue2",
        "created_at": time.time(),
        "note": "Used by orbit_inspect_testbed/live_backend.py for live PRB control.",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {args.output}")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
