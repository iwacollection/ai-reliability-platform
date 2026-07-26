from fastapi import APIRouter


from fastapi.encoders import jsonable_encoder


from services.gateway.app.api.runtime import (
    runtime,
)



router = APIRouter()



@router.get(
    "/traces",
    summary="List execution traces",
)
async def list_traces():

    """
    Query stored agent traces.
    """


    traces = runtime.tracer.list()


    return jsonable_encoder(

        [
            trace.model_dump()

            for trace

            in traces

        ]

    )



@router.get(
    "/traces/{trace_id}",
    summary="Get trace detail",
)
async def get_trace(
    trace_id: str,
):


    trace = runtime.tracer.store.get(
        trace_id
    )


    if trace is None:

        return {

            "success": False,

            "message":
            "trace not found",

        }



    return jsonable_encoder(

        trace.model_dump()

    )