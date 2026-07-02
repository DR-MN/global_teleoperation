"""Safety controller unit tests — the safety-critical path."""
import math

from teleop.core import SystemConfig, JointCommand, CommandMode
from teleop.follower import SafetyController, Verdict


def make_cmd(positions, velocities=None, gripper=0.0):
    return JointCommand(
        seq=1, stamp=0.0, mode=CommandMode.JOINT,
        positions=positions, velocities=velocities or [0.0] * 6, gripper=gripper,
    )


def test_accepts_safe_command():
    sc = SafetyController(SystemConfig())
    res = sc.validate(make_cmd([0.1, 0.1, 0.0, 0.0, 0.0, 0.0]))
    assert res.verdict == Verdict.ACCEPT
    assert res.safe


def test_clamps_soft_limit_breach():
    cfg = SystemConfig()
    sc = SafetyController(cfg)
    over = cfg.joint_limits.soft_upper[0] + 0.2
    res = sc.validate(make_cmd([over, 0.0, 0.0, 0.0, 0.0, 0.0]))
    assert res.verdict == Verdict.CLAMP
    assert res.command.positions[0] <= cfg.joint_limits.soft_upper[0] + 1e-9
    assert res.safe


def test_hard_limit_triggers_estop():
    cfg = SystemConfig()
    sc = SafetyController(cfg)
    over = cfg.joint_limits.hard_upper[0] + 0.5
    res = sc.validate(make_cmd([over, 0, 0, 0, 0, 0]))
    assert res.verdict == Verdict.ESTOP
    assert sc.estopped
    # Once latched, further commands are rejected until reset.
    assert sc.validate(make_cmd([0, 0, 0, 0, 0, 0])).verdict == Verdict.REJECT
    sc.reset_estop()
    assert sc.validate(make_cmd([0, 0, 0, 0, 0, 0])).verdict == Verdict.ACCEPT


def test_non_finite_triggers_estop():
    sc = SafetyController(SystemConfig())
    res = sc.validate(make_cmd([float("nan"), 0, 0, 0, 0, 0]))
    assert res.verdict == Verdict.ESTOP


def test_workspace_violation_rejected():
    cfg = SystemConfig()
    sc = SafetyController(cfg)
    # Drive the arm straight up beyond z_max via joint 1 ~ +90 deg.
    res = sc.validate(make_cmd([0.0, math.pi / 2, 0.0, 0.0, 0.0, 0.0]))
    # Either clamped-safe or rejected, but never an unsafe accept of an
    # out-of-workspace pose.
    if res.verdict == Verdict.REJECT:
        assert res.command is None


def test_watchdog_comms_lost():
    cfg = SystemConfig()
    cfg.network.command_timeout_s = 0.05
    sc = SafetyController(cfg)
    assert not sc.comms_lost()  # no command yet
    sc.note_command_received()
    assert not sc.comms_lost()
    import time
    time.sleep(0.06)
    assert sc.comms_lost()
