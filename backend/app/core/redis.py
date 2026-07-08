"""Redis 客户端单例。

复用方式与 RabbitMQ/MySQL 一致：模块级单例，懒连接，自动重连。
主要用于 citation_matcher 的 embedding 向量缓存（避免对相同 chunk 重复算向量）。
"""
import logging
import time
from typing import Optional

import redis

from app.config import settings

logger = logging.getLogger(__name__)

# 连接失败后的冷却时间（秒）：期间直接返回 None，避免热路径上反复 2s 建连
_RETRY_COOLDOWN = 5.0


class RedisClient:
    """线程安全的 Redis 客户端包装。

    redis-py 自带连接池与自动重连，这里仅做懒初始化与统一配置。
    连接失败时进入冷却期，冷却期内不重试，避免拖慢业务热路径。
    """

    def __init__(self):
        self._client: Optional[redis.Redis] = None
        self._failed_at: Optional[float] = None

    def _build(self) -> redis.Redis:
        return redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            db=settings.REDIS_DB,
            decode_responses=False,  # 向量以 raw bytes 存取
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
            health_check_interval=30,
        )

    @property
    def client(self) -> Optional[redis.Redis]:
        """懒初始化。首次访问时建连；失败进入冷却期返回 None（降级，不阻断业务）。"""
        if self._client is not None:
            return self._client
        # 冷却期内不重试，避免 Redis 不可用时每次调用都阻塞 ~2s
        if self._failed_at is not None and time.time() - self._failed_at < _RETRY_COOLDOWN:
            return None
        try:
            c = self._build()
            c.ping()
            self._client = c
            self._failed_at = None
        except Exception as e:
            logger.warning("Redis connect failed, cache disabled: %s", e)
            self._failed_at = time.time()
            self._client = None
        return self._client

    def ping(self) -> bool:
        """供启动脚本/健康检查使用。失败时清理 _client，保持与 client 属性语义一致。"""
        try:
            if self._client is None:
                c = self._build()
                c.ping()
                self._client = c
                self._failed_at = None
            else:
                self._client.ping()
            return True
        except Exception:
            self._client = None
            self._failed_at = time.time()
            return False


redis_client = RedisClient()
