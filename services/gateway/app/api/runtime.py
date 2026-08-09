from fastapi import (
    APIRouter,
    Body,
    Header,
)

from fastapi.encoders import jsonable_encoder

import traceback


from common.domain.event import StandardEvent


from services.agent_runtime.app.model.context import (
    AgentContext,
)


from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)



router = APIRouter()


runtime = AgentRuntime()



@router.post(
    "/execute",
    summary="Execute Agent Runtime",
)
async def execute_runtime(
    payload: dict = Body(...),

    x_request_id: str | None = Header(
        default=None,
    ),
):
    """
    Execute Agent Runtime pipeline.
    """


    print("=" * 80)

    print("RUNTIME REQUEST START")


    print("REQUEST ID:")

    print(
        x_request_id
    )


    print()

    print("RUNTIME INPUT")

    print(payload)


    print("=" * 80)



    try:


        print("STEP 1: VALIDATE EVENT")


        event = StandardEvent.model_validate(
            payload
        )


        print("EVENT VALIDATED")


        print("STEP 2: CREATE CONTEXT")



        context = AgentContext(

            event=event,

            memory=runtime.memory,

            tools=runtime.tools,

            skills=runtime.skills,

        )



        print("CONTEXT CREATED")



        print("STEP 3: EXECUTE PIPELINE")



        results = await runtime.execute(
            context
        )



        print("PIPELINE FINISHED")



        print("STEP 4: BUILD RESPONSE")



        response = {


            "success": True,


            "request_id": x_request_id,


            "results": [

                result.model_dump()

                for result in results

            ],



            "executions": [

                execution.model_dump()

                for execution in context.executions

            ],



            "evaluations": [

                evaluation.model_dump()

                for evaluation in context.evaluations

            ],



            "traces": [

                trace.model_dump()

                for trace in runtime.tracer.list()

            ],


        }



        print("STEP 5: ENCODE RESPONSE")



        encoded_response = jsonable_encoder(
            response
        )



        print("RESPONSE READY")

        print("=" * 80)



        return encoded_response





    except Exception as exc:



        print("=" * 80)


        print("RUNTIME ERROR")


        print(
            type(exc)
        )


        print(
            str(exc)
        )


        traceback.print_exc()


        print("=" * 80)



        raise