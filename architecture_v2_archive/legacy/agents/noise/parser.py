import json

from services.agent_runtime.app.model.result import AgentResult


def parse_noise_result(
    content: str,
) -> AgentResult:
    """
    Parse LLM response.
    """

    data = json.loads(content)

    return AgentResult(
        agent="noise",
        success=True,
        score=data.get(
            "confidence",
            0,
        ),
        message=(
            "Noise Alert"
            if data.get("noise")
            else "Real Alert"
        ),
        data=data,
    )