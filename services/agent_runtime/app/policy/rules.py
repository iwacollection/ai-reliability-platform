from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
    ActionRisk,
)

from services.agent_runtime.app.policy.models import (
    PolicyDecision,
)



class DefaultHealingPolicy:
    """
    Default SRE healing policy.

    Control automatic remediation safety.
    """


    name = "default_healing_policy"



    def evaluate(
        self,
        action: ActionPlan,
    ) -> PolicyDecision:


        #
        # Low risk action
        #

        if action.risk == ActionRisk.LOW:


            return PolicyDecision(

                allowed=True,

                approved=True,

                require_human=False,

                reason=(
                    "Low risk action "
                    "can execute automatically."
                ),

                policy=self.name,

            )



        #
        # Medium risk action
        #

        if action.risk == ActionRisk.MEDIUM:


            return PolicyDecision(

                allowed=True,

                approved=False,

                require_human=True,

                reason=(
                    "Medium risk action "
                    "requires human approval."
                ),

                policy=self.name,

            )



        #
        # High risk action
        #

        if action.risk == ActionRisk.HIGH:


            return PolicyDecision(

                allowed=False,

                approved=False,

                require_human=True,

                reason=(
                    "High risk action "
                    "cannot execute automatically."
                ),

                policy=self.name,

            )



        return PolicyDecision(

            allowed=False,

            approved=False,

            require_human=True,

            reason=(
                "Unknown action risk."
            ),

            policy=self.name,

        )