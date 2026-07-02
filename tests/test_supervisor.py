"""Supervisor rule-engine tests — deterministic, LLM-independent."""
from teleop.core import SystemConfig, NetworkTelemetry, RobotState, Pose, now
from teleop.agent import Supervisor, Severity


def sup():
    return Supervisor(SystemConfig())


def test_latency_warning_threshold():
    s = sup()
    adv = s.supervise(None, NetworkTelemetry(now(), command_latency_ms=350))
    assert any(a.category == "network" and a.severity == Severity.WARNING for a in adv)


def test_latency_slow_recommendation():
    s = sup()
    adv = s.supervise(None, NetworkTelemetry(now(), command_latency_ms=600))
    assert any("slower" in a.message.lower() for a in adv)


def test_connection_lost_is_critical():
    s = sup()
    adv = s.supervise(None, NetworkTelemetry(now(), connected=False))
    assert adv and adv[0].severity == Severity.CRITICAL
    assert "hold" in adv[0].message.lower()


def test_nominal_has_no_advisories():
    s = sup()
    adv = s.supervise(None, NetworkTelemetry(now(), command_latency_ms=40, packet_loss=0.0))
    assert adv == []


def test_workspace_floor_proximity_warns():
    s = sup()
    cfg = SystemConfig()
    state = RobotState(seq=1, stamp=now(),
                       pose=Pose(x=0.3, y=0.0, z=cfg.workspace.z_min + 0.01))
    adv = s.supervise(state, None)
    assert any(a.category == "workspace" for a in adv)


def test_task_summary_format():
    s = sup()
    out = s.task_summary("Pick red cup", success=True, attempts=1, issues="Minor grasp adjustment")
    assert "Pick red cup" in out and "Success" in out
