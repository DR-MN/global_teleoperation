"""Leader node — real human teleoperation + live operator feed.

Publishes commands to the remote follower over the chosen transport and pushes
the *real* follower state, *measured* network latency, and supervisor advisories
to the operator UI (no synthetic data).

    # Real keyboard teleoperation (default):
    python scripts/run_leader.py --transport ws --url wss://HOST --session demo

    # Hands-free scripted sweep (for unattended testing):
    python scripts/run_leader.py --transport ws --url wss://HOST --session demo --source auto

Keyboard controls (real-time): A/D joint0  W/S joint1  Q/E joint2
                               R/F joint3  T/G joint4  Y/H joint5
                               SPACE toggle gripper     X soft-stop (hold)
Replace the keyboard source with a real leader-arm driver / SpaceMouse / VR
controller by supplying a different target source.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from teleop.core import SystemConfig
from teleop.transport import make_transport
from teleop.leader import LeaderNode
from teleop.network import NetworkMonitor
from teleop.agent import Supervisor

log = logging.getLogger("leader")


def auto_source():
    """Scripted sweep that stays inside the safe workspace (unattended testing)."""
    def src(t: float):
        return ([0.6 * math.sin(0.8 * t), 0.25 + 0.25 * math.sin(0.5 * t), 0, 0, 0, 0],
                0.5 + 0.5 * math.sin(0.3 * t))
    return src


class KeyboardSource:
    """Real-time joint jog from the keyboard. Uses msvcrt on Windows (true
    single-keypress, no Enter) and a cbreak stdin reader on POSIX."""

    KEYMAP = {
        "a": (0, +1), "d": (0, -1), "w": (1, +1), "s": (1, -1),
        "q": (2, +1), "e": (2, -1), "r": (3, +1), "f": (3, -1),
        "t": (4, +1), "g": (4, -1), "y": (5, +1), "h": (5, -1),
    }

    def __init__(self, dof: int = 6, step: float = 0.03) -> None:
        # Start in a safe, above-table pose.
        self.q = [0.0, 0.25, 0.0, 0.0, 0.0, 0.0][:dof] + [0.0] * max(0, dof - 6)
        self.gripper = 0.0
        self.step = step
        self._hold = False
        threading.Thread(target=self._reader, daemon=True).start()

    def _handle(self, ch: str) -> None:
        ch = ch.lower()
        if ch in self.KEYMAP:
            j, sign = self.KEYMAP[ch]
            if j < len(self.q):
                self.q[j] += sign * self.step
                self._hold = False
        elif ch == " ":
            self.gripper = 0.0 if self.gripper > 0.5 else 1.0
        elif ch == "x":
            self._hold = True  # freeze targets at current pose

    def _reader(self) -> None:
        try:
            import msvcrt  # Windows
            while True:
                if msvcrt.kbhit():
                    self._handle(msvcrt.getch().decode("latin-1", "ignore"))
                else:
                    import time as _t
                    _t.sleep(0.005)
        except ImportError:
            # POSIX: put the terminal in cbreak so getch is immediate.
            import termios, tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while True:
                    self._handle(sys.stdin.read(1))
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def __call__(self, _t: float):
        return list(self.q), self.gripper


def main() -> int:
    ap = argparse.ArgumentParser(description="Teleop leader node")
    ap.add_argument("--endpoint", default=os.environ.get("ZENOH_ENDPOINT", ""))
    ap.add_argument("--url", default=os.environ.get("SIGNALING_URL", ""),
                    help="WebSocket server URL for --transport ws")
    ap.add_argument("--session", default=os.environ.get("SESSION_ID", "default"))
    ap.add_argument("--transport", default="ws", choices=["zenoh", "inproc", "ws"])
    ap.add_argument("--source", default="keyboard", choices=["keyboard", "auto"])
    ap.add_argument("--no-feed", action="store_true",
                    help="do not publish the operator feed to the UI")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = SystemConfig.load()
    cfg.transport = args.transport
    cfg.zenoh_endpoint = args.endpoint or None
    cfg.ws_url = args.url or None
    cfg.session_id = args.session

    tx = make_transport(cfg, role="viewer", peer_id="leader-control")
    monitor = NetworkMonitor(cfg)
    source = KeyboardSource(cfg.dof) if args.source == "keyboard" else auto_source()
    leader = LeaderNode(cfg, tx, source, monitor=monitor)
    leader.start()
    log.info("Leader running (session=%s, transport=%s, source=%s). Ctrl-C to stop.",
             args.session, args.transport, args.source)
    if args.source == "keyboard":
        log.info("Keys: A/D D/W/S joints, Q/E R/F T/G Y/H joints, SPACE gripper, X hold")

    # Operator feed: real follower state + measured RTT + live advisories.
    feed = None
    if not args.no_feed and args.transport == "ws":
        from teleop.cloud.telemetry_relay import TelemetryRelay
        supervisor = Supervisor(cfg)
        feed = TelemetryRelay(
            args.url, args.session,
            state_provider=lambda: leader.last_follower_state,
            telemetry_provider=lambda: monitor.telemetry(),
            config=cfg, supervisor=supervisor, peer_id="operator-feed",
        )

    try:
        if feed is not None:
            asyncio.run(feed.run())
        else:
            import time
            while True:
                time.sleep(1.0)
                st = leader.last_follower_state
                t = monitor.telemetry()
                if st:
                    log.info("follower q0=%+.3f q1=%+.3f status=%s | latency=%.0f ms loss=%.1f%%",
                             st.positions[0], st.positions[1], st.status.value,
                             t.command_latency_ms, t.packet_loss * 100)
    except KeyboardInterrupt:
        pass
    finally:
        if feed is not None:
            feed.stop()
        leader.stop()
        tx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
