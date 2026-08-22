from fastapi import APIRouter

router = APIRouter()


@router.post('/incidents/{incident_id}/rollback')
async def rollback(incident_id: str):
    return {
        "incident_id": incident_id,
        "rollback": "started",
        "reason": "verification_failed",
        "evidence": [],
    }
