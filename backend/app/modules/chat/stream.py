import json
from starlette.responses import StreamingResponse


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_stream(generator):
    """将 token generator 包装为 SSE StreamingResponse"""

    def event_generator():
        for token in generator:
            if token.startswith("__ERROR__:"):
                error_msg = token[len("__ERROR__:"):]
                yield sse_event("error", {"message": error_msg})
                return
            yield sse_event("token", {"content": token})
        yield sse_event("done", {"message_id": None})

    return StreamingResponse(event_generator(), media_type="text/event-stream")
