import json
from starlette.responses import StreamingResponse


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_stream(agent_gen):
    """将 async agent generator 包装为 SSE StreamingResponse"""

    async def event_generator():
        async for ev in agent_gen:
            event_type = ev.get("event", "message")
            event_data = ev.get("data", {})
            yield sse_event(event_type, event_data)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
