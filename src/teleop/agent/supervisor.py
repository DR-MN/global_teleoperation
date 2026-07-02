"""TeleOp-RO supervisor.

Two layers, deliberately separated:

1. A *deterministic rule engine* that encodes the safety- and network-awareness
   rules from the spec (latency thresholds, joint/workspace proximity, comms
   loss, stream loss). This layer is authoritative and never depends on an LLM —
   safety must not hinge on a 1B model's output.

2. An *optional LLM layer* (TeleOp-RO on Llama 3.2 1B via a pluggable backend)
   that turns structured state into concise operator-facing guidance and task
   summaries. Backends: ``MockLLM`` (offline, deterministic) or ``OllamaLLM``.

The supervisor never drives motors; it emits advisories. Critical advisories can
be wired to trigger the safety controller's E-stop by the integrator.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional

from ..core.config import SystemConfig
from ..core.types import NetworkTelemetry, RobotState
from .prompt import load_system_prompt

log = logging.getLogger(__name__)


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2


@dataclass
class Advisory:
    severity: Severity
    category: str          # "network" | "joint" | "workspace" | "safety" | "vision"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.name}] {self.category}: {self.message}"


# --------------------------------------------------------------------------- #
# Deterministic rule engine
# --------------------------------------------------------------------------- #
class RuleEngine:
    def __init__(self, config: SystemConfig) -> None:
        self.cfg = config
        self.jl = config.joint_limits
        self.ws = config.workspace
        self.net = config.network

    def evaluate(self, state: Optional[RobotState],
                 telemetry: Optional[NetworkTelemetry]) -> List[Advisory]:
        out: List[Advisory] = []
        if telemetry is not None:
            out.extend(self._network(telemetry))
        if state is not None:
            out.extend(self._joints(state))
            out.extend(self._workspace(state))
        # Highest severity first for operator triage.
        out.sort(key=lambda a: a.severity, reverse=True)
        return out

    def _network(self, t: NetworkTelemetry) -> List[Advisory]:
        out: List[Advisory] = []
        if not t.connected:
            out.append(Advisory(Severity.CRITICAL, "network",
                                "Connection lost. Recommend follower hold position."))
            return out
        if t.command_latency_ms > self.net.slow_latency_ms:
            out.append(Advisory(Severity.WARNING, "network",
                                f"Latency {t.command_latency_ms:.0f} ms > "
                                f"{self.net.slow_latency_ms:.0f} ms. Recommend slower movements."))
        elif t.command_latency_ms > self.net.warn_latency_ms:
            out.append(Advisory(Severity.WARNING, "network",
                                f"Latency {t.command_latency_ms:.0f} ms exceeds "
                                f"{self.net.warn_latency_ms:.0f} ms. Operator warned."))
        if t.packet_loss > self.net.max_packet_loss:
            out.append(Advisory(Severity.WARNING, "network",
                                f"Packet loss {t.packet_loss * 100:.1f}% elevated."))
        if not t.streams_ok:
            out.append(Advisory(Severity.WARNING, "vision",
                                "Video stream degraded. Switch to global camera guidance."))
        return out

    def _joints(self, s: RobotState) -> List[Advisory]:
        out: List[Advisory] = []
        for i, q in enumerate(s.positions[: self.cfg.dof]):
            span = self.jl.soft_upper[i] - self.jl.soft_lower[i]
            margin = 0.05 * span
            if q <= self.jl.soft_lower[i] + margin or q >= self.jl.soft_upper[i] - margin:
                out.append(Advisory(Severity.WARNING, "joint",
                                    f"Joint {i} near soft limit ({q:.2f} rad)."))
        return out

    def _workspace(self, s: RobotState) -> List[Advisory]:
        out: List[Advisory] = []
        p, ws = s.pose, self.ws
        # Warn within 3 cm of any workspace boundary — mirrors the spec example
        # "may collide with table edge in approximately 5 cm".
        m = 0.03
        if p.z <= ws.z_min + m:
            out.append(Advisory(Severity.WARNING, "workspace",
                                f"End-effector {(p.z - ws.z_min) * 100:.1f} cm above lower "
                                "Z boundary (table). Lower slowly."))
        if (p.x <= ws.x_min + m or p.x >= ws.x_max - m or
                p.y <= ws.y_min + m or p.y >= ws.y_max - m):
            out.append(Advisory(Severity.WARNING, "workspace",
                                "End-effector near horizontal workspace boundary."))
        return out


# --------------------------------------------------------------------------- #
# LLM backends
# --------------------------------------------------------------------------- #
class LLMBackend(abc.ABC):
    @abc.abstractmethod
    def complete(self, system_prompt: str, user_message: str) -> str:
        ...


class MockLLM(LLMBackend):
    """Deterministic offline backend: echoes the highest-severity advisory as a
    concise operator instruction. Lets the full pipeline run with no model."""

    def complete(self, system_prompt: str, user_message: str) -> str:
        for line in user_message.splitlines():
            if line.startswith("[CRITICAL]") or line.startswith("[WARNING]"):
                return line
        return "Nominal. Continue teleoperation."


class OllamaLLM(LLMBackend):
    """Llama 3.2 1B via a local Ollama server, per the spec's target model."""

    def __init__(self, model: str = "llama3.2:1b",
                 host: str = "http://localhost:11434") -> None:
        self.model = model
        self.host = host

    def complete(self, system_prompt: str, user_message: str) -> str:
        import urllib.request
        import json
        body = json.dumps({
            "model": self.model,
            "system": system_prompt,
            "prompt": user_message,
            "stream": False,
            "options": {"temperature": 0.0},  # deterministic per spec
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read()).get("response", "").strip()
        except Exception:
            log.exception("Ollama request failed; falling back to mock")
            return MockLLM().complete(system_prompt, user_message)


# --------------------------------------------------------------------------- #
# Supervisor
# --------------------------------------------------------------------------- #
class Supervisor:
    def __init__(self, config: SystemConfig,
                 llm: Optional[LLMBackend] = None,
                 prompt_path: Optional[str] = None) -> None:
        self.cfg = config
        self.rules = RuleEngine(config)
        self.llm = llm or MockLLM()
        self.system_prompt = load_system_prompt(prompt_path)

    def supervise(self, state: Optional[RobotState],
                  telemetry: Optional[NetworkTelemetry]) -> List[Advisory]:
        """Authoritative, deterministic advisories. Safe to call at any rate."""
        return self.rules.evaluate(state, telemetry)

    def guidance(self, state: Optional[RobotState],
                 telemetry: Optional[NetworkTelemetry],
                 operator_request: str = "") -> str:
        """Natural-language operator guidance via the LLM, grounded in the
        deterministic advisories so the model cannot contradict the rules."""
        advisories = self.supervise(state, telemetry)
        ctx_lines = [str(a) for a in advisories] or ["Nominal."]
        user = "Current advisories:\n" + "\n".join(ctx_lines)
        if operator_request:
            user += f"\n\nOperator request: {operator_request}"
        return self.llm.complete(self.system_prompt, user)

    def task_summary(self, task: str, success: bool, attempts: int,
                     issues: str = "") -> str:
        return (
            f"Task:\n{task}\n\n"
            f"Result:\n{'Success' if success else 'Failure'}\n\n"
            f"Attempts:\n{attempts}\n\n"
            f"Observed issues:\n{issues or 'None'}"
        )
