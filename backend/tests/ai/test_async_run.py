"""run_async 单测(2026-09-14): 复用单事件循环, 防共享 httpx client 跨已关闭循环。

回归背景: report 33 解读第二次 generate_report 时抛
openai.APIConnectionError(Event loop is closed), 根因是 asyncio.run 反复开关循环
而 langchain_openai 缓存了进程级共享 async httpx client。
"""
import asyncio
import threading

from app.ai.async_run import run_async


def test_run_async_returns_result():
    async def val():
        return 42

    assert run_async(val()) == 42


def test_run_async_reuses_one_loop_per_thread():
    async def cur():
        return asyncio.get_running_loop()

    assert run_async(cur()) is run_async(cur())


def test_loop_bound_resource_survives_across_calls():
    """核心回归: 绑定到首个循环的 asyncio 原语跨调用仍可用。

    `asyncio.run` 每次新循环 → 复用该原语会抛 "bound to a different event loop";
    run_async 固定单循环 → 不抛。
    """
    holder = {}

    async def create():
        holder["lock"] = asyncio.Lock()
        await holder["lock"].acquire()
        return asyncio.get_running_loop()

    async def use():
        holder["lock"].release()
        await holder["lock"].acquire()
        return asyncio.get_running_loop()

    l1 = run_async(create())
    l2 = run_async(use())
    assert l1 is l2


def test_run_async_uses_distinct_loop_per_thread():
    async def cur():
        return asyncio.get_running_loop()

    main_loop = run_async(cur())
    box = {}

    def worker():
        box["loop"] = run_async(cur())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert box["loop"] is not main_loop
