import asyncio


from services.harness.runner.runner import (
    HarnessRunner,
)


from services.harness.loader import (
    HarnessCaseLoader,
)


from services.harness.evaluator.matcher import (
    HarnessEvaluator,
)



async def main():

    case_name = (
        "pod_high_cpu.json"
    )


    #
    # Load raw case
    #

    loader = HarnessCaseLoader()


    case = loader.load(
        case_name
    )


    expected = case.get(
        "expected",
        {}
    )


    #
    # Run Agent Runtime
    #

    runner = HarnessRunner()


    result = await runner.run(
        case_name
    )


    #
    # Evaluate
    #

    evaluator = HarnessEvaluator()


    evaluation = evaluator.evaluate(

        expected,

        result,

    )



    print("=" * 80)

    print(
        "HARNESS RESULT"
    )

    print("=" * 80)



    print(

        {

            "case":
            case["case_id"],


            "passed":
            evaluation["passed"],


            "evaluation":
            evaluation,


        }

    )


    print("=" * 80)



    #
    # Debug Runtime Result
    #
    # Show:
    # - agent results
    # - healing data
    # - approval
    # - sandbox
    #

    print("=" * 80)

    print(
        "RUNTIME RESULT"
    )

    print("=" * 80)


    print(

        result

    )


    print("=" * 80)




if __name__ == "__main__":

    asyncio.run(
        main()
    )