from services.agent_runtime.app.agent.base import (
    BaseAgent,
)

from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
)

from services.agent_runtime.app.agents.healing.prompt import (
    build_healing_prompt,
)

from services.agent_runtime.app.agents.healing.parser import (
    parse_healing_result,
)



class HealingAgent(BaseAgent):
    """
    Auto healing suggestion agent.
    """


    @property
    def agent_type(self):

        return "remediation"



    @property
    def depends_on(self):

        return [
            "root_cause"
        ]



    @property
    def provides(self):

        return [
            "remediation_plan"
        ]



    def __init__(
        self,
        llm_client: LLMClient,
    ) -> None:

        self.llm_client = llm_client



    @property
    def name(self) -> str:

        return "healing"



    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:


        #
        # Get RCA result
        #

        rca_result = context.variables.get(
            "rca",
            {},
        )



        #
        # Build healing prompt
        #

        prompt = build_healing_prompt(
            context.event,
            rca_result,
        )



        response = await self.llm_client.chat(
            ChatRequest(

                system_prompt=(
                    "You are an SRE healing assistant."
                ),

                user_prompt=prompt,

            )
        )



        result = parse_healing_result(
            response.content,
        )



        #
        # Healing result
        #

        healing_data = result.data



        action_data = healing_data.get(
            "action"
        )



        action_plan = None



        #
        # Convert dict to ActionPlan
        #

        if action_data:


            action_plan = ActionPlan(

                type=action_data.get(
                    "type"
                ),

                target=action_data.get(
                    "target",
                    "",
                ),

                risk=ActionRisk(
                    healing_data.get(
                        "risk",
                        "medium",
                    )
                ),

            )


            healing_data[
                "action"
            ] = action_plan.model_dump()



        #
        # Policy + Approval + Sandbox
        #

        if action_plan and context.sandbox_policy:


            #
            # Policy validation
            #

            policy_result = (
                context.sandbox_policy.validate(
                    action_plan.model_dump()
                )
            )


            healing_data[
                "policy"
            ] = policy_result.model_dump()



            #
            # Need approval
            #

            if (
                policy_result.allowed
                and
                policy_result.require_approval
                and
                context.approval
            ):


                approval_request = (
                    await context.approval.create_approval(
                        action=action_plan,
                        reason=(
                            healing_data.get(
                                "reason",
                                "Healing action requires approval",
                            )
                        ),
                    )
                )


                healing_data[
                    "approval"
                ] = approval_request.model_dump()



            #
            # Execute directly
            #

            elif (

                policy_result.allowed

                and

                not policy_result.require_approval

                and

                context.sandbox

            ):


                sandbox_result = (
                    await context.sandbox.execute(
                        action_plan.model_dump()
                    )
                )


                healing_data[
                    "sandbox"
                ] = sandbox_result.model_dump()



        #
        # Save healing result
        #

        context.variables[
            "healing"
        ] = healing_data



        result.data = healing_data



        return result