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

from services.gateway.app.api.evidence import (
    router as evidence_router,
)

from services.gateway.app.api.graph import (
    router as graph_router,
)

from services.gateway.app.api.remediation import (
    router as remediation_router,
)

from services.gateway.app.api.rollback import (
    router as rollback_router,
)

api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(runtime_router, prefix="/runtime", tags=["Runtime"])
api_router.include_router(traces_router, prefix="/observability", tags=["Observability"])
api_router.include_router(incidents_router, prefix="/api", tags=["Incidents"])
api_router.include_router(evidence_router, prefix="/api", tags=["Evidence"])
api_router.include_router(graph_router, prefix="/api", tags=["Investigation Graph"])
api_router.include_router(remediation_router, prefix="/api", tags=["Remediation"])
api_router.include_router(rollback_router, prefix="/api", tags=["Rollback"])
