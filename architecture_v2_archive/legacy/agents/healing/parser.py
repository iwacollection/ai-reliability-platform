import json


from services.agent_runtime.app.model.result import (
    AgentResult,
)



def parse_healing_result(
    content: str,
) -> AgentResult:
    """
    Parse LLM healing response.

    Normalize healing decision output.

    Expected format:

    {
        "action": {
            "type": "",
            "target": ""
        },
        "risk": "",
        "reason": "",
        "rollback": "",
        "verification": "",
        "approval_required": true
    }

    """



    data = json.loads(
        content
    )



    #
    # Normalize action
    #

    action = data.get(
        "action"
    )



    #
    # Case:
    #
    # "action": "restart_pod"
    #

    if isinstance(
        action,
        str,
    ):


        action = {

            "type": action,

            "target": "unknown",

        }



    #
    # Missing action
    #

    if action is None:


        action = {

            "type":
            "none",

            "target":
            "unknown",

        }



    #
    # Normalize action fields
    #

    action = {


        "type":
        action.get(
            "type",
            "none",
        ),


        "target":
        action.get(
            "target",
            "unknown",
        ),

    }



    data[
        "action"
    ] = action



    #
    # Normalize optional fields
    #

    data.setdefault(
        "risk",
        "unknown",
    )


    data.setdefault(
        "reason",
        "",
    )


    data.setdefault(
        "rollback",
        "",
    )


    data.setdefault(
        "verification",
        "",
    )


    data.setdefault(
        "approval_required",
        True,
    )



    return AgentResult(

        agent="healing",

        success=True,

        score=1.0,

        message=action.get(
            "type",
            "no_action",
        ),

        data=data,

    )