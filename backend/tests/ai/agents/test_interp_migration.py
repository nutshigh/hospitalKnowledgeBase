from unittest.mock import patch, MagicMock


def test_worker_calls_run_interpretation_agent():
    """worker 消费任务后调 run_interpretation_agent"""
    with patch("app.modules.interpretation.worker.run_interpretation_agent") as mock_run, \
         patch("app.modules.interpretation.worker.get_hospital_db") as mock_db_fn:
        mock_db = MagicMock()
        mock_db_fn.return_value = iter([mock_db])

        from app.modules.interpretation.worker import handle_interpretation_task
        handle_interpretation_task({
            "payload": {"report_id": 1, "hospital_id": "H001"}
        })
        mock_run.assert_called_once()


def test_worker_skips_event_messages():
    """worker 跳过 event 通知消息"""
    with patch("app.modules.interpretation.worker.run_interpretation_agent") as mock_run:
        from app.modules.interpretation.worker import handle_interpretation_task
        handle_interpretation_task({"payload": {"event": "interpretation_done"}})
        mock_run.assert_not_called()
