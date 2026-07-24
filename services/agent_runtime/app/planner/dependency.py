from dataclasses import dataclass


@dataclass
class AgentDependency:

    agent: str

    missing: list[str]

    satisfied: list[str]