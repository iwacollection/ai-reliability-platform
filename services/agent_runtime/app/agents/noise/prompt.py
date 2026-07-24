from common.domain.event import StandardEvent


def build_noise_prompt(
    event: StandardEvent,
) -> str:
    """
    Build prompt for noise detection.
    """

    return f"""
You are an SRE AI assistant.

Analyze this alert.

Alert name:
{event.signal.name}

Severity:
{event.signal.severity}

Message:
{event.signal.message}

Resource:
{event.resources[0].name if event.resources else "unknown"}

Return JSON only:

{{
    "noise": true,
    "confidence": 0.0,
    "reason": ""
}}
"""