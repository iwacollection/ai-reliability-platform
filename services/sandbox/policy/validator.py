from typing import Any



class PolicyValidationResult:
    """
    Policy validation result.
    """


    def __init__(
        self,
        allowed: bool,
        reason: str,
        require_approval: bool = False,
    ) -> None:


        self.allowed = allowed

        self.reason = reason

        self.require_approval = (
            require_approval
        )



    def model_dump(
        self,
    ) -> dict[str, Any]:

        return {

            "allowed":
                self.allowed,

            "reason":
                self.reason,

            "require_approval":
                self.require_approval,

        }




class SandboxPolicyValidator:
    """
    Validate sandbox actions.


    Rules:

    safe action:
        allow


    dangerous action:
        deny


    sensitive action:
        require approval

    """



    def __init__(
        self,
    ) -> None:


        self.blocked_actions = {

            "delete_cluster",

            "drop_database",

            "shutdown_service",

        }


        self.approval_actions = {

            "restart_deployment",

            "scale_deployment",

            "increase_memory_limit",

        }



    def validate(
        self,
        action: dict[str, Any],
    ) -> PolicyValidationResult:
        """
        Validate action.
        """



        action_type = action.get(
            "type",
            "",
        )



        if action_type in self.blocked_actions:


            return PolicyValidationResult(

                allowed=False,

                reason=(
                    "Dangerous action blocked"
                ),

            )



        if action_type in self.approval_actions:


            return PolicyValidationResult(

                allowed=True,

                reason=(
                    "Action requires approval"
                ),

                require_approval=True,

            )



        return PolicyValidationResult(

            allowed=True,

            reason=(
                "Action allowed"
            ),

        )