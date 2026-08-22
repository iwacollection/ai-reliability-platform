from common.domain.event import (
    StandardEvent,
)


def build_rca_prompt(
    event: StandardEvent,
    evidence: list,
    history: dict | None = None,
) -> str:
    """
    Build RCA analysis prompt.
    """


    resource = (
        event.resources[0].name
        if event.resources
        else "unknown"
    )


    evidence_text = ""


    for item in evidence:

        source = item.get(
            "source",
            "unknown",
        )

        resource_name = item.get(
            "resource",
            "unknown",
        )

        facts = item.get(
            "facts",
            [],
        )


        evidence_text += f"""

Source:
{source}

Resource:
{resource_name}

Facts:
"""


        for fact in facts:

            evidence_text += f"- {fact}\n"



    #
    # Historical memory
    #

    history_text = ""


    if history:

        history_text = f"""

Historical Similar Incident:

{history}


Use this historical incident as reference.
Do not blindly copy it.
Combine it with current evidence.


"""



    return f"""
You are an SRE root cause analysis assistant.

Analyze this production incident.


Alert:

Name:
{event.signal.name}


Message:

{event.signal.message}


Resource:

{resource}


Collected Evidence:


{evidence_text}



{history_text}


Analyze:

1. What is the root cause?
2. Which evidence supports the conclusion?
3. Give confidence score.



Return JSON only:


{{
    "root_cause": "",
    "confidence": 0.0,
    "evidence": []
}}

"""