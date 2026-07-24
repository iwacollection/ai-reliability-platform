from typing import Any


def build_diagnosis_prompt(
    event,
    evidence: list[dict[str, Any]],
) -> str:


    return f"""
You are an SRE diagnosis assistant.

Analyze the incident using evidence.


Alert:

{event.signal.name}


Message:

{event.signal.message}


Evidence:

{evidence}


Return JSON only:

{{
    "suspected_cause":"",
    "confidence":0.0,
    "reason":""
}}
"""