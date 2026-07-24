import json

from services.agent_runtime.app.model.result import AgentResult


def parse_rca_result(
    content: str,
) -> AgentResult:

    data = json.loads(
        content
    )

    return AgentResult(
        agent="rca",
        success=True,
        score=data.get(
            "confidence",
            0,
        ),
        message=data.get(
            "root_cause",
            "Unknown",
        ),
        data=data,
    )