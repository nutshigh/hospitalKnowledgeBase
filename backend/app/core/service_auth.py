"""服务间鉴权依赖（新增文件）。

用途：供 sz-mana（Java 管理后台）等服务端转发调用统计接口时使用，
不依赖终端用户 JWT，改为校验请求头：
  - X-Api-Key      ：服务端共享密钥（配置项 STAT_SERVICE_API_KEY）
  - X-Hospital-Id  ：目标医院 ID，用于注入 hospital context（路由到 hospital_<id> 库）

设计说明：
1. 与既有 get_current_user / require_role 完全独立，不改动任何既有鉴权逻辑；
2. STAT_SERVICE_API_KEY 未配置（空）时，依赖直接拒绝服务（避免裸奔）；
3. 仅新增端点使用本依赖，既有端点鉴权方式不变。
"""
from fastapi import Header

from app.config import settings
from app.middleware.hospital_context import set_current_hospital_id
from app.utils.exceptions import UnauthorizedException, ValidationException


async def require_service_client(
    x_api_key: str = Header(default="", description="服务端共享密钥"),
    x_hospital_id: str = Header(default="", description="目标医院ID"),
) -> str:
    expected = getattr(settings, "STAT_SERVICE_API_KEY", "")
    if not expected:
        raise UnauthorizedException(detail="Service API key not configured on server")
    if not x_api_key or x_api_key != expected:
        raise UnauthorizedException(detail="Invalid service API key")
    if not x_hospital_id:
        raise ValidationException(detail="X-Hospital-Id header required")
    set_current_hospital_id(x_hospital_id)
    return x_hospital_id
