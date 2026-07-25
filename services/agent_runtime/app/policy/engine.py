from services.agent_runtime.app.action.models import (
    ActionPlan,
)

from services.agent_runtime.app.policy.models import (
    PolicyDecision,
)

from services.agent_runtime.app.policy.rules import (
    DefaultHealingPolicy,
)



class PolicyEngine:
    """
    Policy evaluation engine.

    Responsible for deciding whether
    an action can be executed.
    """


    def __init__(self):

        self.policies = [

            DefaultHealingPolicy()

        ]



    def evaluate(
        self,
        action: ActionPlan,
    ) -> PolicyDecision:
        """
        Evaluate action against policies.
        """


        for policy in self.policies:


            decision = policy.evaluate(
                action
            )


            return decision



        return PolicyDecision(

            allowed=False,

            approved=False,

            require_human=True,

            reason=(
                "No policy matched."
            ),

            policy="none",

        )