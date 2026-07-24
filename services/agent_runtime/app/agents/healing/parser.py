import json

from services.agent_runtime.app.model.result import (
    AgentResult,
)


def parse_healing_result(
    content: str,
) -> AgentResult:

    data = json.loads(
        content
    )

    return AgentResult(
        agent="healing",
        success=True,
        score=1.0,
        message=data.get(
            "action",
            "no_action",
        ),
        data=data,
    )