"""身份证后六位 → hospital_id 的外部解析客户端(批量上传分发用)。

契约暂定最简约定,接口文档后提供时只改 `_build_request` / `_parse_response`
两个函数内部即可,对外 `resolve_hospital` 签名保持不变。
"""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("app.batch.extract.resolver")


class ResolverUnavailableError(Exception):
    """外部接口不可用(超时/5xx/网络错)。调用方应走批次级重试,而非短路。"""


_shared_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.Client(timeout=settings.EXTERNAL_RESOLVER_TIMEOUT)
    return _shared_client


def _build_request(id_suffix: str) -> dict:
    # 契约暂定:POST body {"id_suffix": "12345X"}。接口文档后提供时改这里。
    return {"id_suffix": id_suffix}


def _parse_response(resp: httpx.Response) -> Optional[str]:
    if resp.status_code != 200:
        if 400 <= resp.status_code < 500:
            logger.warning("resolver 4xx status=%s body=%s",
                           resp.status_code, getattr(resp, "text", "")[:200])
            return None  # 明确 not found → 无匹配
        raise ResolverUnavailableError(f"resolver http {resp.status_code}")
    try:
        data = resp.json() or {}
    except ValueError:
        raise ResolverUnavailableError("resolver bad json")
    return data.get("hospital_id") or None


def resolve_hospital(id_suffix: str) -> Optional[str]:
    """返回 hospital_id(匹配)/ None(明确无匹配)。宕机抛 ResolverUnavailableError。"""
    url = settings.EXTERNAL_RESOLVER_URL
    if not url:
        return None  # 未配置:默认无匹配,防误落库
    client = _get_client()
    try:
        resp = client.post(url, json=_build_request(id_suffix))
        return _parse_response(resp)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise ResolverUnavailableError(str(e)) from e
