#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time

import rclpy
from std_msgs.msg import String


CONTROL_TOPIC = os.environ.get("ORBIT_CONTROL_TOPIC", "/orbit_inspect/control")
CONTROL_ACK_TOPIC = os.environ.get("ORBIT_CONTROL_ACK_TOPIC", "/orbit_inspect/control_ack")


def main() -> int:
    acks: list[dict] = []

    def on_ack(msg: String) -> None:
        try:
            acks.append(json.loads(msg.data))
        except Exception as exc:
            acks.append({"component": "unknown", "status": "parse_error", "error": str(exc)})

    rclpy.init()
    node = rclpy.create_node("orbit_inspect_live_probe")
    pub = node.create_publisher(String, CONTROL_TOPIC, 10)
    node.create_subscription(String, CONTROL_ACK_TOPIC, on_ack, 10)

    payload = {
        "probe": True,
        "source": "check_live_stack",
        "created_at": time.time(),
    }
    msg = String()
    msg.data = json.dumps(payload)

    max_subscribers = 0
    max_ack_publishers = 0
    deadline = time.time() + float(os.environ.get("ORBIT_PROBE_TIMEOUT_SEC", "4.0"))
    last_publish = 0.0

    while time.time() < deadline:
        now = time.time()
        max_subscribers = max(max_subscribers, node.count_subscribers(CONTROL_TOPIC))
        max_ack_publishers = max(max_ack_publishers, node.count_publishers(CONTROL_ACK_TOPIC))
        if now - last_publish >= 0.25:
            pub.publish(msg)
            last_publish = now
        rclpy.spin_once(node, timeout_sec=0.05)
        components = {ack.get("component") for ack in acks}
        if {"isaac", "factory_relay"}.issubset(components):
            break

    print(json.dumps({
        "control_subscribers": max_subscribers,
        "ack_publishers": max_ack_publishers,
        "acks": acks,
        "components": sorted({str(ack.get("component")) for ack in acks if ack.get("component")}),
    }))

    node.destroy_node()
    rclpy.shutdown()

    components = {ack.get("component") for ack in acks}
    return 0 if {"isaac", "factory_relay"}.issubset(components) else 1


if __name__ == "__main__":
    raise SystemExit(main())
