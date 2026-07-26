from typing import Any


from services.sandbox.executor.base import (
    BaseSandboxExecutor,
    SandboxExecutionResult,
)



class LocalSandboxExecutor(
    BaseSandboxExecutor
):
    """
    Local sandbox executor.


    Current:

    - simulate action execution
    - no real side effect


    Future:

    - Kubernetes dry-run
    - Terraform plan
    - Shell sandbox
    - Container sandbox

    """



    async def execute(
        self,
        action: dict[str, Any],
    ) -> SandboxExecutionResult:
        """
        Execute action in local sandbox.


        Example:

        {
            "type": "restart_deployment",

            "target":
                "payment-api"
        }

        """



        action_type = action.get(
            "type",
            "unknown",
        )


        target = action.get(
            "target",
            "unknown",
        )



        #
        # Sandbox simulation
        #

        output = {


            "mode":
                "sandbox",


            "dry_run":
                True,


            "action_type":
                action_type,


            "target":
                target,


            "message":
                "Action simulated successfully",

        }



        return SandboxExecutionResult(

            success=True,

            action=action_type,

            message=(
                "Sandbox execution completed"
            ),

            output=output,

        )