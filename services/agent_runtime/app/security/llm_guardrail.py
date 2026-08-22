"""LLM and tool safety guardrails."""

from dataclasses import dataclass


@dataclass
class GuardrailDecision:
    allowed: bool
    reason: str


class LLMGuardrail:
    BLOCKED_PATTERNS = [
        "ignore previous instructions",
        "reveal system prompt",
    ]

    def check_prompt(self, prompt: str) -> GuardrailDecision:
        value = prompt.lower()
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in value:
                return GuardrailDecision(False, f"blocked pattern: {pattern}")
        return GuardrailDecision(True, "accepted")

    def check_tool(self, tool: str, action: str, allowed_actions: set[str]):
        return action in allowed_actions
