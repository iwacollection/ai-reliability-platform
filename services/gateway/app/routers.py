from fastapi import APIRouter


from services.gateway.app.api.health import (
    router as health_router,
)


from services.gateway.app.api.runtime import (
    router as runtime_router,
)


from services.gateway.app.api.traces import (
    router as traces_router,
)


from services.gateway.app.api.incidents import (
    router as incidents_router,
)


api_router = APIRouter()


api_router.include_router(
    health_router,
)


api_router.include_router(
    runtime_router,
    prefix="/runtime",
    tags=["Runtime"],
)


api_router.include_router(
    traces_router,
    prefix="/observability",
    tags=["Observability"],
)


api_router.include_router(
    incidents_router,
    prefix="/api",
    tags=["Incidents"],
)
