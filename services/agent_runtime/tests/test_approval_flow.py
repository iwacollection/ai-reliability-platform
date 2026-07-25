import pytest


from services.agent_runtime.app.approval.service import (
    ApprovalService,
)


from services.agent_runtime.app.runtime.action_runtime import (
    ActionRuntime,
)



@pytest.mark.asyncio
async def test_approval_resume_flow():

    #
    # Use same approval service
    #

    approval_service = ApprovalService()


    action_runtime = ActionRuntime(
        approval_service=approval_service
    )



    #
    # Simulate healing result
    #

    healing_result = {

        "agent":
        "healing",


        "success":
        True,


        "score":
        1.0,


        "message":
        "increase memory limit",


        "data":

        {

            "action":
            "increase_memory_limit",


            "target":
            "payment-api",


            "risk":
            "medium",


            "reason":
            "Pod memory limit exceeded",

        }

    }



    #
    # Step 1:
    # create approval
    #

    plan, result = await action_runtime.execute(
        healing_result
    )


    assert result["status"] == (
        "pending_approval"
    )


    approval_id = result["approval_id"]



    #
    # Step 2:
    # human approve
    #

    approval = await approval_service.approve(
        approval_id
    )


    assert approval.status.value == (
        "approved"
    )



    #
    # Step 3:
    # resume execution
    #

    execution = await action_runtime.resume(
        approval_id
    )



    assert execution["success"] is True


    assert execution["action"] == (
        "increase_memory_limit"
    )