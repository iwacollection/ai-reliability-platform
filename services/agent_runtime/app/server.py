from fastapi import FastAPI

from services.agent_runtime.app.api.runtime import (
    router as runtime_router,
)


app = FastAPI(
    title="AI Reliability Agent Runtime",
    version="0.1.0",
)


app.include_router(
    runtime_router,
    prefix="/runtime",
    tags=["Runtime"],
)


@app.get("/health")
async def health():

    return {
        "status": "ok",
        "service": "agent-runtime",
        "version": "0.1.0",
    }