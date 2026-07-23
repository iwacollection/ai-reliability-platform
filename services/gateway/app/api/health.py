from fastapi import APIRouter

from services.gateway.app.core.config import get_settings

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    settings = get_settings()

    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.version,
    }