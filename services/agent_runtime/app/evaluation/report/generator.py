from services.agent_runtime.app.evaluation.models import (
    EvaluationResult,
)

from services.agent_runtime.app.evaluation.report.models import (
    EvaluationReport,
)



class EvaluationReportGenerator:
    """
    Generate aggregated evaluation report.
    """



    def generate(
        self,
        evaluations: list[EvaluationResult],
    ) -> EvaluationReport:


        total = len(
            evaluations
        )


        passed = len(
            [
                item
                for item in evaluations
                if item.passed
            ]
        )


        failed = total - passed



        if total:


            pass_rate = round(
                passed / total,
                4,
            )


            overall_score = round(
                sum(
                    item.score
                    for item in evaluations
                )
                /
                total,
                4,
            )


        else:


            pass_rate = 0.0

            overall_score = 0.0



        #
        # Aggregate LLM Metrics
        #

        total_llm_calls = 0

        total_prompt_tokens = 0

        total_completion_tokens = 0

        total_tokens = 0

        total_llm_latency = 0.0



        for item in evaluations:


            metrics = item.metrics



            total_llm_calls += metrics.get(
                "llm_calls",
                0,
            )


            llm_tokens = metrics.get(
                "llm_tokens",
                {},
            )


            total_prompt_tokens += llm_tokens.get(
                "prompt_tokens",
                0,
            )


            total_completion_tokens += llm_tokens.get(
                "completion_tokens",
                0,
            )


            total_tokens += llm_tokens.get(
                "total_tokens",
                0,
            )


            total_llm_latency += metrics.get(
                "llm_latency_ms",
                0.0,
            )



        avg_llm_latency_ms = 0.0


        if total_llm_calls:


            avg_llm_latency_ms = round(

                total_llm_latency
                /
                total_llm_calls,

                4,

            )



        return EvaluationReport(


            total_agents=total,


            passed_agents=passed,


            failed_agents=failed,


            pass_rate=pass_rate,


            overall_score=overall_score,


            evaluations=evaluations,


            metrics={


                "evaluation_count":
                total,


                "total_llm_calls":
                total_llm_calls,


                "total_prompt_tokens":
                total_prompt_tokens,


                "total_completion_tokens":
                total_completion_tokens,


                "total_tokens":
                total_tokens,


                "avg_llm_latency_ms":
                avg_llm_latency_ms,


            },

        )