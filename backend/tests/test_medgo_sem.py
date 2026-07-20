import asyncio
import importlib

import pytest


@pytest.mark.asyncio
async def test_concurrency_capped_at_n(monkeypatch):
    monkeypatch.setenv("MEDGO_MAX_CONCURRENCY", "2")
    import app.ai.llm as llm_mod

    importlib.reload(llm_mod)
    from app.ai.llm import medgo_sem, _guarded

    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def task():
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1

    await asyncio.gather(*[_guarded(task()) for _ in range(5)])
    assert peak <= 2, f"peak exceeded N: {peak}"
    assert peak == 2  # 确实用满了


@pytest.mark.asyncio
async def test_release_on_cancel(monkeypatch):
    import app.ai.llm as llm_mod

    monkeypatch.setenv("MEDGO_MAX_CONCURRENCY", "1")
    importlib.reload(llm_mod)
    from app.ai.llm import medgo_sem, _guarded

    async def slow():
        await asyncio.sleep(10)

    t = asyncio.create_task(_guarded(slow()))
    await asyncio.sleep(0.05)
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    # 关键: sem 必须已释放, 新协程可立即获取
    async def quick():
        return 42

    r = await asyncio.wait_for(_guarded(quick()), timeout=1.0)
    assert r == 42