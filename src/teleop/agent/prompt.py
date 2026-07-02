"""Loads the TeleOp-RO supervisor system prompt.

The system prompt is the project's ``ctx.txt`` spec. Loading it from disk (rather
than hardcoding) keeps the agent's behavior contract in one editable place.
"""
from __future__ import annotations

from pathlib import Path

_DEFAULT = (
    "You are TeleOp-RO, a teleoperation supervision agent. Be concise, technical, "
    "and deterministic. Prioritize protecting humans, then hardware, then stable "
    "teleoperation, then task completion. Output only operationally useful "
    "information. Prefer stopping over continuing uncertain actions."
)


def load_system_prompt(path: str | None = None) -> str:
    p = Path(path) if path else Path(__file__).resolve().parents[3] / "ctx.txt"
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text or _DEFAULT
    except Exception:
        return _DEFAULT
