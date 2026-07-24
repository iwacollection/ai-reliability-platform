import asyncio


from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


from services.agent_runtime.app.evaluation.scenario.factory import (
    create_scenario_registry,
)


from services.agent_runtime.app.evaluation.replay.engine import (
    ScenarioReplayEngine,
)


from services.agent_runtime.app.evaluation.runner import (
    ScenarioRunner,
)


from services.agent_runtime.app.evaluation.report.generator import (
    EvaluationReportGenerator,
)


from services.agent_runtime.app.evaluation.assertion.engine import (
    AssertionEngine,
)



async def main():


    runtime = AgentRuntime()



    scenario_registry = (
        create_scenario_registry()
    )



    replay_engine = ScenarioReplayEngine(
        runtime
    )



    runner = ScenarioRunner(

        scenario_registry,

        replay_engine,

    )



    results = await runner.run_all()



    assertion_engine = AssertionEngine()



    print()

    print("=" * 80)

    print(
        "Scenario Replay Finished"
    )

    print("=" * 80)



    for item in results:


        print()

        print(
            "Scenario:",
            item["scenario"],
        )



        context = item["context"]



        report_generator = (
            EvaluationReportGenerator()
        )



        report = (
            report_generator.generate(
                context.evaluations
            )
        )



        print()

        print(
            "Evaluation Report"
        )


        print(
            report.model_dump()
        )



        print()

        print(
            "Scenario Assertion"
        )



        assertion = (
            assertion_engine.assert_scenario(

                scenario_registry.get(
                    item["scenario"]
                ),

                context,

            )
        )



        print(
            assertion.model_dump()
        )



if __name__ == "__main__":

    asyncio.run(main())