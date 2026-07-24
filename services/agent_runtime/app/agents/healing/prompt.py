from common.domain.event import StandardEvent


def build_healing_prompt(
    event: StandardEvent,
    rca_result: dict,
) -> str:


    resource = (
        event.resources[0].name
        if event.resources
        else "unknown"
    )


    root_cause = rca_result.get(
        "root_cause",
        "unknown",
    )


    confidence = rca_result.get(
        "confidence",
        0,
    )


    evidence = rca_result.get(
        "evidence",
        [],
    )


    evidence_text = ""


    for item in evidence:

        evidence_text += (
            f"- {item}\n"
        )



    return f"""
You are an SRE auto healing assistant.

Analyze this incident and propose remediation.



Alert:

Name:
{event.signal.name}


Message:

{event.signal.message}


Resource:

{resource}



Root Cause Analysis:

Root Cause:
{root_cause}


Confidence:
{confidence}



Supporting Evidence:

{evidence_text}



Decide:

1. What action should be executed?
2. What resource should be changed?
3. What is the risk level?



Return JSON only:


{{
    "action": "",
    "target": "",
    "risk": "",
    "reason": "",
    "approval_required": true
}}

"""