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
    - execution metrics
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



        #
        # LLM Metrics
        #

        llm_calls = (
            execution
            .metadata
            .get(
                "llm_calls",
                [],
            )
        )


        total_prompt_tokens = 0

        total_completion_tokens = 0

        total_tokens = 0

        total_latency = 0.0

        models = []



        for call in llm_calls:


            total_prompt_tokens += call.get(
                "prompt_tokens",
                0,
            )


            total_completion_tokens += call.get(
                "completion_tokens",
                0,
            )


            total_tokens += call.get(
                "total_tokens",
                0,
            )


            total_latency += call.get(
                "duration_ms",
                0,
            )


            model = call.get(
                "model"
            )


            if model:

                models.append(
                    model
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


                "llm_tokens": {

                    "prompt_tokens":
                    total_prompt_tokens,


                    "completion_tokens":
                    total_completion_tokens,


                    "total_tokens":
                    total_tokens,

                },


                "llm_latency_ms":
                round(
                    total_latency,
                    4,
                ),


                "llm_models":
                models,

            },

        )