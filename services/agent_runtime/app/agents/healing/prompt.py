from common.domain.event import StandardEvent


def build_healing_prompt(
    event: StandardEvent,
) -> str:

    resource = (
        event.resources[0].name
        if event.resources
        else "unknown"
    )

    return f"""
You are an SRE auto healing assistant.

Analyze this incident.

Alert:
{event.signal.name}

Message:
{event.signal.message}

Resource:
{resource}


Return JSON only:

{{
    "action": "",
    "target": "",
    "risk": ""
}}
"""