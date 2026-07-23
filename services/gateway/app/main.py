from fastapi import FastAPI

from services.gateway.app.core.config import get_settings
from services.gateway.app.core.logger import logger
from services.gateway.app.routers import api_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
)

app.include_router(api_router)


@app.on_event("startup")
async def startup():
    logger.info("Gateway started")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Gateway stopped")