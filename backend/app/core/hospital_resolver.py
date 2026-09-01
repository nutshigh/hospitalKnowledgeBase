"""身份证后六位 → hospital_id 的外部解析客户端(批量上传分发 + app-login 用)。

对接 baUser 开放接口 searchUser:
  GET {EXTERNAL_RESOLVER_URL}?realName={name}&idCardLast6={id_suffix}
统一信封 {code, msg, data},data 为数组 [{realName, idCardLast6, orgId}, ...]。
orgId 即 hospital_id(用户确认),直接 str(orgId) 返回;对 data 做精确过滤防串号。
"""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("app.batch.extract.resolver")


class ResolverUnavailableError(Exception):
    """外部接口不可用(超时/5xx/业务 code!=200/坏 JSON)。调用方应走批次级重试,而非短路。"""


_shared_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.Client(timeout=settings.EXTERNAL_RESOLVER_TIMEOUT)
    return _shared_client


def _build_params(name: str, id_suffix: str) -> dict:
    return {"realName": name, "idCardLast6": id_suffix}


def _parse_response(resp: httpx.Response, name: str, id_suffix: str) -> Optional[str]:
    if resp.status_code != 200:
        if 400 <= resp.status_code < 500:
            logger.warning("resolver 4xx status=%s body=%s",
                           resp.status_code, getattr(resp, "text", "")[:200])
            return None  # 明确 not found → 无匹配
        raise ResolverUnavailableError(f"resolver http {resp.status_code}")
    try:
        payload = resp.json() or {}
    except ValueError:
        raise ResolverUnavailableError("resolver bad json")
    if payload.get("code") != 200:
        raise ResolverUnavailableError(
            f"resolver business code={payload.get('code')} msg={payload.get('msg')}")
    data = payload.get("data") or []
    if not isinstance(data, list):
        raise ResolverUnavailableError("resolver bad data shape")
    org_ids = {
        str(item["orgId"])
        for item in data
        if item.get("orgId")
        and item.get("realName") == name
        and item.get("idCardLast6") == id_suffix
    }
    if not org_ids:
        return None
    if len(org_ids) > 1:
        logger.warning("resolver ambiguous name=%s suffix=%s org_ids=%s",
                       name, id_suffix, org_ids)
        return None
    return next(iter(org_ids))


def resolve_hospital(name: str, id_suffix: str) -> Optional[str]:
    """返回 hospital_id(匹配)/ None(明确无匹配)。宕机抛 ResolverUnavailableError。"""
    url = settings.EXTERNAL_RESOLVER_URL
    if not url:
        return None  # 未配置:默认无匹配,防误落库
    client = _get_client()
    try:
        resp = client.get(url, params=_build_params(name, id_suffix))
        return _parse_response(resp, name, id_suffix)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise ResolverUnavailableError(str(e)) from e
