"""单事件循环执行异步 LLM 调用(2026-09-14, report 33 修复)。

langchain_openai 对默认 async httpx client 做进程级缓存
(`_cached_async_httpx_client`), 所有 ChatOpenAI 实例共享同一个 httpx.AsyncClient。
反复 `asyncio.run()` 每次新建/关闭事件循环时, 共享 client 连接池里的 transport 会
残留绑定在已关闭循环上, 下一次调用关闭连接即抛
`RuntimeError('Event loop is closed')` → `openai.APIConnectionError`, 解读/生成失败。
本模块为每个线程复用一个常驻事件循环, 共享 client 始终绑定同一循环。
"""
import asyncio
import threading

_local = threading.local()


def run_async(coro):
    """在**本线程**的常驻事件循环上执行 coro(仅限同步上下文调用)。"""
    loop = getattr(_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _local.loop = loop
    return loop.run_until_complete(coro)
