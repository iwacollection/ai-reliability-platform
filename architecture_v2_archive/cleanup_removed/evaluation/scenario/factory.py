from services.agent_runtime.app.evaluation.scenario.registry import (
    ScenarioRegistry,
)


from services.agent_runtime.app.evaluation.scenario.cases.pod_oom_killed import (
    create_pod_oom_killed_scenario,
)



def create_scenario_registry() -> ScenarioRegistry:
    """
    Create evaluation scenario registry.
    """


    registry = ScenarioRegistry()



    registry.register(

        create_pod_oom_killed_scenario()

    )



    return registry