from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser


@pytest.fixture
def _doctor_user():
    return CurrentUser(user_id=1, role="doctor", hospital_id="H001")


def test_knowledge_import_200_with_jwt_hospital_context(_doctor_user, tmp_path, monkeypatch):
    """POST /api/v1/knowledge/import 应能从 JWT 取 hospital_id。

    回归:knowledge router 的 _get_db 依赖 ContextVar(_get_hospital_id),
    而该 ContextVar 只由 get_current_user 设置,且 knowledge 端点未依赖
    get_current_user,导致所有 knowledge 端点返回 400 Hospital context required。
    修复:仿 chat router(commit 1d25ab1),_get_db 直接依赖 get_current_user,
    从 JWT payload 取 hospital_id。
    """
    import app.main as main_mod

    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: _doctor_user

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

    monkeypatch.setattr("app.modules.knowledge.router.settings.FILE_STORAGE_ROOT", str(tmp_path))

    with patch("app.modules.knowledge.router.get_hospital_db", side_effect=_fake_get_hospital_db), \
         patch("app.modules.knowledge.router.service.import_from_file", return_value=1) as mock_import:
        # No `with` → startup/shutdown events do NOT fire (sweeper stays off).
        client = TestClient(app)
        r = client.post(
            "/api/v1/knowledge/import",
            files={"file": ("guide.txt", "糖尿病知识内容\n血糖正常范围\n".encode("utf-8"), "text/plain")},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"imported": 1, "filename": "guide.txt"}
        mock_import.assert_called_once()
        # hospital_id 应取自 JWT,而不是空的 ContextVar
        assert mock_import.call_args.args[1] == "H001"

    app.dependency_overrides.pop(get_current_user, None)


def test_knowledge_entries_list_200_with_jwt_hospital_context(_doctor_user, tmp_path, monkeypatch):
    """GET /api/v1/knowledge/entries 不再报 400 Hospital context required。"""
    import app.main as main_mod

    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: _doctor_user

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

    with patch("app.modules.knowledge.router.get_hospital_db", side_effect=_fake_get_hospital_db):
        client = TestClient(app)
        r = client.get("/api/v1/knowledge/entries")
        assert r.status_code == 200, r.text
        assert r.json()["items"] == []

    app.dependency_overrides.pop(get_current_user, None)
