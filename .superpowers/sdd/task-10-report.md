## Status
Done. `interpretation/worker.py` refactored to per-queue (urgent+normal+bulk) consume, bulk window timegate (`is_bulk_window_now()` → `_NackOnce(requeue=True)`), delay-queue retry via `rabbitmq.publish_retry` (no more `time.sleep`), retry_count<3 vs >=3 routing, and `BatchService.increment_progress(... "interp_ok"|"failed")` on batch-id/file-id presence. `_RETRY_BACKOFFS` and `_maybe_requeue_for_retry` deleted.

## Commits
- `feat(interp): worker per-queue + bulk 时段 + 延迟队列重试 + running 跳过`

## Test Summary
6 new tests pass; Task 8/9 regression (12 tests) all green — 18 passed total.

```
tests/test_interp_worker_bulk.py::test_bulk_non_window_nack PASSED       [ 16%]
tests/test_interp_worker_bulk.py::test_normal_not_time_bound PASSED      [ 33%]
tests/test_interp_worker_bulk.py::test_retry_count_1_publish_retry PASSED [ 50%]
tests/test_interp_worker_bulk.py::test_retry_count_3_raises_and_failed PASSED [ 66%]
tests/test_interp_worker_bulk.py::test_success_increments_interp_ok PASSED [ 83%]
tests/test_interp_worker_bulk.py::test_comparison_summary_failure_doesnt_break PASSED [100%]
...
======================= 18 passed, 49 warnings in 5.08s ========================
```

## Self-Review
- Per-queue consume (urgent+normal+bulk) mirrors Task 9 `report/worker.py` exactly.
- Bulk window check first thing in callback, raises `_NackOnce(requeue=True)` outside window — matches binding.
- `time.sleep` removed; retry routed via `rabbitmq.publish_retry(..., expiration_ms=backoff_for_retry(retries - 1), batch_id=batch_id)`.
- "Running skip" satisfied by `run_interpretation_agent`'s existing `== completed` early-return (binding #2 — no duplicate pre-check added).
- Re-fetch `ReportInterpretation` in except clause to read `retry_count` (binding #1); `>=3` → re-raise (DLQ) `+` `BatchService.increment_progress(... "failed")`; `<3` → publish_retry + ack.
- Comparison summary still attempted, failures logged, don't break interp success (binding #6).
- `payload` (incl. batch_id/file_id) preserved verbatim into retry body (binding #4). `routing_key` from `message["_routing_key"]` (binding #5).
- `interp_graph.py`/service files untouched.

## Concerns
- Test relies on `run_interpretation_agent` mock returning immediately; real retry_count book-keeping inside that function is not covered by these tests (already covered by agent-level tests).
- `BatchService` mocked in tests; real `increment_progress` behavior covered by Task 9's batch_service tests.