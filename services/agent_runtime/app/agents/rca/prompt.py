from common.domain.event import StandardEvent


def build_rca_prompt(
    event: StandardEvent,
    metrics: dict,
) -> str:
    """
    Build RCA prompt with observability data.
    """


    return f"""
You are an SRE root cause analysis assistant.

Analyze this alert using the provided metrics.


Alert:

{event.signal.name}


Message:

{event.signal.message}


Resource:

{event.resources[0].name if event.resources else "unknown"}


Observability Metrics:

{metrics}


Analyze the possible root cause.


Return JSON only:


{{
    "root_cause": "",
    "confidence": 0.0
}}
"""