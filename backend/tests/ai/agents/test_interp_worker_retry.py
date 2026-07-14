from unittest.mock import patch, MagicMock


def _make_interp(status="pending", retry_count=1):
    interp = MagicMock()
    interp.status = status
    interp.retry_count = retry_count
    return interp


def test_worker_requeues_on_failure_when_retryable():
    """失败但 retry_count<3（status='pending'）时把 interpretation 任务重入队。"""
    db = MagicMock()
    interp = _make_interp(status="pending", retry_count=1)
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = interp

    with patch("app.modules.interpretation.worker.run_interpretation_agent", side_effect=RuntimeError("vllm 400")), \
         patch("app.modules.interpretation.worker.get_hospital_db", return_value=iter([db])), \
         patch("app.modules.interpretation.worker.time.sleep") as mock_sleep, \
         patch("app.modules.interpretation.worker.rabbitmq") as mock_rabbitmq, \
         patch("app.modules.interpretation.worker.TaskMessage") as mock_task_msg:
        from app.modules.interpretation.worker import handle_interpretation_task
        handle_interpretation_task({"payload": {"report_id": 23, "hospital_id": "H001"}})

    # 等到退避期后才 publish；退避取 _RETRY_BACKOFFS[0]=10s
    mock_sleep.assert_called_once_with(10)
    mock_rabbitmq.publish.assert_called_once()
    tm_kwargs = mock_task_msg.call_args.kwargs
    assert tm_kwargs["task_type"] == "interpretation"
    assert tm_kwargs["hospital_id"] == "H001"
    assert tm_kwargs["payload"] == {"report_id": 23, "hospital_id": "H001"}


def test_worker_does_not_requeue_when_failed():
    """retry_count>=3 时 interp_graph 把 status 置 'failed'，worker 不重入队。"""
    db = MagicMock()
    interp = _make_interp(status="failed", retry_count=3)
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = interp

    with patch("app.modules.interpretation.worker.run_interpretation_agent", side_effect=RuntimeError("vllm 400")), \
         patch("app.modules.interpretation.worker.get_hospital_db", return_value=iter([db])), \
         patch("app.modules.interpretation.worker.time.sleep") as mock_sleep, \
         patch("app.modules.interpretation.worker.rabbitmq") as mock_rabbitmq, \
         patch("app.modules.interpretation.worker.TaskMessage"):
        from app.modules.interpretation.worker import handle_interpretation_task
        handle_interpretation_task({"payload": {"report_id": 23, "hospital_id": "H001"}})

    mock_sleep.assert_not_called()
    mock_rabbitmq.publish.assert_not_called()


def test_worker_does_not_requeue_when_no_interp_row():
    """找不到 interp 行时不重入队（避免对无关 report 永久争用）。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    with patch("app.modules.interpretation.worker.run_interpretation_agent", side_effect=RuntimeError("boom")), \
         patch("app.modules.interpretation.worker.get_hospital_db", return_value=iter([db])), \
         patch("app.modules.interpretation.worker.time.sleep") as mock_sleep, \
         patch("app.modules.interpretation.worker.rabbitmq") as mock_rabbitmq, \
         patch("app.modules.interpretation.worker.TaskMessage"):
        from app.modules.interpretation.worker import handle_interpretation_task
        handle_interpretation_task({"payload": {"report_id": 999, "hospital_id": "H001"}})

    mock_sleep.assert_not_called()
    mock_rabbitmq.publish.assert_not_called()


def test_worker_publish_failure_does_not_raise():
    """publish 自身抛错也不能再传播，避免 worker 被 kill 出循环。"""
    db = MagicMock()
    interp = _make_interp(status="pending", retry_count=2)
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = interp
    mock_rabbitmq = MagicMock()
    mock_rabbitmq.publish.side_effect = Exception("amqp down")

    with patch("app.modules.interpretation.worker.run_interpretation_agent", side_effect=RuntimeError("vllm 400")), \
         patch("app.modules.interpretation.worker.get_hospital_db", return_value=iter([db])), \
         patch("app.modules.interpretation.worker.time.sleep"), \
         patch("app.modules.interpretation.worker.rabbitmq", mock_rabbitmq), \
         patch("app.modules.interpretation.worker.TaskMessage"):
        from app.modules.interpretation.worker import handle_interpretation_task
        # 不抛
        handle_interpretation_task({"payload": {"report_id": 23, "hospital_id": "H001"}})
    mock_rabbitmq.publish.assert_called_once()