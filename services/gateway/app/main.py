from fastapi import FastAPI

from services.gateway.app.core.config import get_settings
from services.gateway.app.core.logger import logger
from services.gateway.app.routers import api_router
from services.gateway.app.api.webhook import router as webhook_router


settings = get_settings()

app = FastAPI(
    title="AI Reliability Gateway",
    version="0.1.0",
)

app.include_router(
    webhook_router,
    prefix="/api/v1/webhooks",
    tags=["Webhook"],
)

app.include_router(api_router)


@app.on_event("startup")
async def startup():
    logger.info("Gateway started")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Gateway stopped")