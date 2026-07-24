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

            },

        )