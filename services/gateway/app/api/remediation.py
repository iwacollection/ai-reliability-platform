from fastapi import APIRouter

router = APIRouter()


@router.get('/incidents/{incident_id}/remediation')
async def get_remediation_plan(incident_id: str):
    return {
        "incident_id": incident_id,
        "plan": {
            "action": "restart_unhealthy_pods",
            "risk": "medium",
            "requires_approval": True,
        },
        "policy": {
            "auto_execute": False,
            "approval_required": True,
        },
    }


@router.post('/incidents/{incident_id}/approval')
async def approve_remediation(incident_id: str):
    return {
        "incident_id": incident_id,
        "approval": "approved",
        "next": "execution",
    }


@router.get('/incidents/{incident_id}/verification')
async def verification_status(incident_id: str):
    return {
        "incident_id": incident_id,
        "status": "pending",
        "checks": [
            "health_check",
            "error_rate",
            "latency",
        ],
    }
