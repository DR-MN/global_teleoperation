"""TeleOp-RO supervision agent: deterministic rule engine + pluggable LLM."""
from .supervisor import (
    Supervisor,
    RuleEngine,
    Advisory,
    Severity,
    LLMBackend,
    MockLLM,
    OllamaLLM,
)
from .prompt import load_system_prompt

__all__ = [
    "Supervisor",
    "RuleEngine",
    "Advisory",
    "Severity",
    "LLMBackend",
    "MockLLM",
    "OllamaLLM",
    "load_system_prompt",
]
