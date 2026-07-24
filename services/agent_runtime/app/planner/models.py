from dataclasses import dataclass


@dataclass
class AgentDependency:
    """
    Agent dependency analysis result.
    """

    agent: str

    missing: list[str]

    satisfied: list[str]