import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_process_chat_stream_yields_sse_events():
    """process_chat_stream 调 run_chat_agent 并转 SSE 事件"""
    with patch("app.modules.chat.service.run_chat_agent") as mock_run:
        async def fake_agent(*args, **kwargs):
            yield {"event": "tool_status", "data": {"tool": "search_knowledge", "status": "start"}}
            yield {"event": "token", "data": {"content": "你好"}}
            yield {"event": "done", "data": {"message_id": 1}}
        mock_run.side_effect = fake_agent

        from app.modules.chat import service
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.id = 1
        mock_session.report_id = None
        mock_session.title = "test"

        with patch.object(service, "save_message"), \
             patch.object(service, "get_messages", return_value=[]):
            events = []
            async for ev in service.process_chat_stream(mock_db, mock_session, "你好", 1, "张三"):
                events.append(ev)
            assert len(events) == 3
            assert events[1]["event"] == "token"
