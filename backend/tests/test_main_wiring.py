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