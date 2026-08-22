from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    domain: str
    triggers: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    investigation_plan: list[str] = field(default_factory=list)
    decision_rules: dict[str, Any] = field(default_factory=dict)
    safety_policy: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillDefinition:
    metadata: SkillMetadata
    instructions: str
