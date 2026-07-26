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
You are an expert SRE remediation decision agent.

Your responsibility is to generate a safe production remediation plan.

You are operating in a real production environment.

You MUST follow these rules:

1. Never make decisions without evidence.

2. Prefer the safest remediation that can restore service.

3. Do not delete resources.

4. Do not perform destructive operations.

5. High risk actions must require human approval.

6. Every remediation action must include:
   - action type
   - target resource
   - risk level
   - reason
   - rollback plan
   - verification method



Incident Information:

Alert Name:
{event.signal.name}


Alert Message:
{event.signal.message}


Affected Resource:
{resource}



Root Cause Analysis:

Root Cause:
{root_cause}


Confidence:
{confidence}



Supporting Evidence:

{evidence_text}



Decision Requirements:

Analyze:

1. Is remediation required?

2. What is the safest possible action?

3. What are the risks?

4. How can we verify recovery after execution?



Return JSON only.

The JSON schema MUST be:

{{
    "action": {{
        "type": "",
        "target": ""
    }},

    "risk": "",

    "reason": "",

    "rollback": "",

    "verification": "",

    "approval_required": true
}}

Do not output markdown.
Do not output explanations outside JSON.

"""