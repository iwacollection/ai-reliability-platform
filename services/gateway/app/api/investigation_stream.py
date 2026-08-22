from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json
import asyncio

router = APIRouter()


@router.get('/incidents/{incident_id}/investigation/stream')
async def investigation_stream(incident_id: str):
    async def events():
        samples = [
            {
                'type': 'agent_thought',
                'payload': {'message': 'Analyzing incident context'},
            },
            {
                'type': 'tool_call',
                'payload': {'tool': 'kubernetes-mcp'},
            },
            {
                'type': 'mcp_response',
                'payload': {'status': 'completed'},
            },
        ]

        for event in samples:
            yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(events(), media_type='text/event-stream')
