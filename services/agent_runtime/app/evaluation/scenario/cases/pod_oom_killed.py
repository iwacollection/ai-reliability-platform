from services.agent_runtime.app.evaluation.scenario.models import (
    ScenarioDefinition,
)


def create_pod_oom_killed_scenario() -> ScenarioDefinition:
    """
    Pod OOMKilled incident scenario.

    Resource scope is explicit so replayed remediation and verification use
    the same namespace and cluster without falling back to a default scope.
    """

    return ScenarioDefinition(

        name="pod_oom_killed",


        description=(

            "Production pod crashed because "
            "memory configuration changed."

        ),


        event={

            "alertname":
            "PodHighCPU",


            "severity":
            "critical",


            "resource":
            "payment-api",


            "namespace":
            "payment",


            "cluster":
            "production-a",

        },


        expected={

            "noise":
            False,


            "root_cause_contains":
            "OOMKilled",


            "healing_action":
            "increase_memory_limit",

        },


        metadata={

            "category":
            "kubernetes",


            "type":
            "incident",

        },

    )
