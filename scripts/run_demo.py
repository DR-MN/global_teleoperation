"""Hardware-free end-to-end demo of the teleoperation pipeline.

Runs the full control plane in one process over the in-process transport:

    leader node -> transport -> follower controller -> safety -> mock arm
                              <- state feedback <-

Then exercises the supervisor's network/safety rules and records a short
demonstration episode. No ROS2, Zenoh, model, or hardware required.

    python scripts/run_demo.py
"""
from __future__ import annotations

import logging
import math
import time

# Allow running directly from a checkout without installation.
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from teleop.core import SystemConfig, NetworkTelemetry, now
from teleop.transport import InProcTransport
from teleop.follower import FollowerController
from teleop.leader import LeaderNode
from teleop.agent import Supervisor
from teleop.recording import Recorder


def banner(text: str) -> None:
    print(f"\n=== {text} ===")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = SystemConfig.load()

    # Simulate a real link: 20 ms one-way latency, 2% packet loss.
    tx = InProcTransport(latency_s=0.02, drop_rate=0.02)

    follower = FollowerController(cfg, tx)

    # Leader source: smooth sinusoidal sweep on joints 0 and 1, gripper cycling.
    def source(t: float):
        positions = [0.6 * math.sin(0.8 * t), 0.4 * math.sin(0.5 * t), 0, 0, 0, 0]
        gripper = 0.5 + 0.5 * math.sin(0.3 * t)
        return positions, gripper

    leader = LeaderNode(cfg, tx, source)
    supervisor = Supervisor(cfg)
    recorder = Recorder(cfg.recording_dir)

    banner("Starting leader + follower (mirroring for 2.5 s)")
    follower.start()
    leader.start()
    recorder.start("Pick red cup", attempts=1)

    t_end = time.time() + 2.5
    while time.time() < t_end:
        state = leader.last_follower_state
        if state is not None:
            recorder.record(state)
        time.sleep(0.05)

    state = leader.last_follower_state
    if state:
        print(f"Follower tracking: q0={state.positions[0]:+.3f} q1={state.positions[1]:+.3f} "
              f"status={state.status.value} gripper={state.gripper_position:.2f}")
    print("Follower diagnostics:", follower.stats)

    banner("Simulating comms loss (leader stops) — watchdog should hold")
    leader.stop()
    time.sleep(1.0)  # exceeds command_timeout_s -> safe-state hold
    state = follower.arm.read_state()
    print(f"Follower status after comms loss: {state.status.value} "
          f"(holding={follower._holding})")

    banner("Supervisor advisories across network scenarios")
    scenarios = {
        "nominal":      NetworkTelemetry(now(), command_latency_ms=45, packet_loss=0.01),
        "high latency": NetworkTelemetry(now(), command_latency_ms=350, packet_loss=0.02),
        "very high":    NetworkTelemetry(now(), command_latency_ms=620, packet_loss=0.03),
        "comms lost":   NetworkTelemetry(now(), connected=False),
    }
    for name, tele in scenarios.items():
        advisories = supervisor.supervise(state, tele)
        print(f"\n[{name}]")
        for a in advisories or ["(no advisories)"]:
            print("  ", a)
        print("  guidance:", supervisor.guidance(state, tele))

    banner("Finishing recording")
    path = recorder.stop(success=True, notes="Minor grasp adjustment")
    print("Episode written to:", path)
    print("\n" + supervisor.task_summary(
        "Pick red cup", success=True, attempts=1, issues="Minor grasp adjustment"))

    follower.stop()
    tx.close()
    banner("Demo complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
