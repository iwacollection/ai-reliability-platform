from services.agent_runtime.app.model.result import (
    AgentResult,
)


from services.agent_runtime.app.observability.execution import (
    AgentExecutionRecord,
)


from services.agent_runtime.app.evaluation.models import (
    EvaluationResult,
)


from services.agent_runtime.app.evaluation.base import (
    BaseEvaluator,
)



class DefaultEvaluator(BaseEvaluator):
    """
    Basic agent evaluator.

    Validate:
    - execution success
    - result score
    """


    @property
    def name(self):

        return "default"



    async def evaluate(
        self,
        result: AgentResult,
        execution: AgentExecutionRecord,
    ) -> EvaluationResult:


        passed = (

            result.success

            and

            result.score >= 0.8

        )


        return EvaluationResult(

            agent=result.agent,

            passed=passed,

            score=result.score,

            message=(

                "Agent evaluation passed"

                if passed

                else

                "Agent evaluation failed"

            ),

            metrics={

                "execution_time_ms":
                execution.duration_ms,

                "memory_hit":
                execution.memory_hit,

                "tool_calls":
                len(
                    execution.tool_calls
                ),

                "llm_calls":
                execution.llm_calls,

            },

        )