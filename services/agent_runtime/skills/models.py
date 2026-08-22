from dataclasses import dataclass, field


@dataclass
class SkillDefinition:
    name: str
    version: str
    description: str
    input_schema: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
