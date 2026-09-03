import asyncio
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser


@pytest.fixture
def _admin_user():
    return CurrentUser(user_id=1, role="admin", hospital_id="H001")


def test_T13_batch_route_registered_in_main_app():
    """batch_router endpoints should be wired under /api/v1/reports in app.main."""
    import app.main as main_mod

    paths = {getattr(r, "path", None) for r in main_mod.app.routes}
    assert "/api/v1/reports/batches" in paths


def test_T13_batch_list_route_not_shadowed_by_report_detail(_admin_user, tmp_path):
    """GET /api/v1/reports/batches 必须命中 batch_router,而不是被 report_router 的
    GET /{report_id}(int 参数)影子匹配成 422(batch_router 需先于 report_router include)。"""
    import app.main as main_mod

    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: _admin_user

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def _fake_get_hospital_db(hospital_id):
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    with patch("app.modules.report.batch_router.get_hospital_db",
               side_effect=_fake_get_hospital_db), \
         patch("app.modules.report.batch_router.rabbitmq"):
        client = TestClient(app)
        r = client.get("/api/v1/reports/batches")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == [] and body["total"] == 0

    app.dependency_overrides.pop(get_current_user, None)


def test_T13_batch_post_returns_200(_admin_user, tmp_path):
    """Round-trip through app.main: POST /api/v1/reports/batches — no startup events."""
    import app.main as main_mod

    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: _admin_user

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def _fake_get_hospital_db(hospital_id):
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    with patch("app.modules.report.batch_router.get_hospital_db",
               side_effect=_fake_get_hospital_db), \
         patch("app.modules.report.batch_service.rabbitmq"), \
         patch("app.modules.report.batch_router.rabbitmq") as router_mq, \
         patch("app.modules.report.batch_service.settings.FILE_STORAGE_ROOT",
               str(tmp_path)), \
         patch("app.modules.report.batch_router.settings",
               MagicMock(FILE_STORAGE_ROOT=str(tmp_path),
                        BATCH_ARCHIVE_MAX_SIZE=10 * 1024 * 1024 * 100)):
        router_mq.consume_dead.return_value = []
        # No `with` → startup/shutdown events do NOT fire (sweeper stays off).
        client = TestClient(app)
        r = client.post("/api/v1/reports/batches", data={"filename": "test.zip"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "batch_id" in body

    app.dependency_overrides.pop(get_current_user, None)


def test_T13_sweeper_starts_on_startup_and_cancels_on_shutdown():
    """startup hook should create the sweeper task; shutdown should cancel it."""
    import app.main as main_mod

    started = []

    async def _fake_start():
        started.append(True)
        # block forever until cancelled
        await asyncio.Event().wait()

    # Patch the name imported into app.main so the startup handler calls the fake.
    with patch.object(main_mod, "start_sweeper", _fake_start):
        # app already built at import time; startup handlers fire on context enter
        with TestClient(main_mod.app) as client:
            assert started, "start_sweeper should have run on startup"
            task = getattr(main_mod.app.state, "batch_sweeper_task", None)
            assert task is not None
            assert not task.cancelled()

        # after shutdown context exit, task should be cancelled
        assert main_mod.app.state.batch_sweeper_task.cancelled()


def test_T14_setup_logging_called_in_create_app(monkeypatch):
    """create_app() 进入时 setup_logging 应被调用一次。"""
    import app.main as main_mod
    calls = {"count": 0}

    def _spy(default_level: str = "INFO"):
        calls["count"] += 1

    # main.py 用 `from app.core.logging_config import setup_logging` 把名字绑到
    # app.main 模块上,所以 patch app.main 的 binding,而不是源模块。
    monkeypatch.setattr(main_mod, "setup_logging", _spy)
    # 避免 create_app() 真连 Milvus
    monkeypatch.setattr("app.ai.config.ensure_milvus_started", lambda: None)
    main_mod.create_app()
    assert calls["count"] >= 1, "create_app 必须调用 setup_logging()"


def test_T15_sweeper_logger_namespaced_under_app_batch(monkeypatch):
    """sweeper 启动回调应使用 app.batch.sweeper logger。

    Re-edit block 把 getLogger 放在 `@app.on_event("startup")` 回调里,
    只有 lifespan start 才触发,因此用 TestClient 进入上下文以 fire startup。
    """
    import app.main as main_mod
    import logging
    from fastapi.testclient import TestClient

    async def _noop_async():
        return None

    # main.py 顶部 `from app.core.batch_sweeper import start as start_sweeper`,
    # binding 在 app.main.start_sweeper;patch 它以防真起 sweeper 后台任务。
    monkeypatch.setattr(main_mod, "start_sweeper", _noop_async)
    monkeypatch.setattr("app.ai.config.ensure_milvus_started", lambda: None)
    # 防止 setup_logging 真写盘到 /data/logs
    monkeypatch.setattr(main_mod, "setup_logging", lambda: None)

    names_seen = set()
    orig_get = logging.getLogger

    def _spy_get(name=None):
        if name and "batch" in name:
            names_seen.add(name)
        return orig_get(name)

    monkeypatch.setattr(logging, "getLogger", _spy_get)

    app = main_mod.create_app()
    with TestClient(app):  # 触发 lifespan startup
        pass
    assert "app.batch.sweeper" in names_seen, (
        "sweeper 应使用 app.batch.sweeper 命名空间,实际见到:%r" % names_seen
    )