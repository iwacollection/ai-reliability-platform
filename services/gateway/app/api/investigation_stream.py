from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json

from services.agent_runtime.app.events.event_bus import agent_event_bus

router = APIRouter()


@router.get('/incidents/{incident_id}/investigation/stream')
async def investigation_stream(incident_id: str):
    async def events():
        async for event in agent_event_bus.subscribe(incident_id):
            payload = {
                'type': event.event_type,
                'incident_id': event.incident_id,
                'payload': event.payload,
                'timestamp': event.timestamp,
            }
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        events(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )
