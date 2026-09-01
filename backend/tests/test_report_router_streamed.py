import os
import io
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser
from app.modules.report.router import router as report_router
from app.modules.report import service as report_service
from app.models.base import Base
from app.modules.report.models import ReportTask, ReportInfo  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation  # noqa: F401


def _bootstrap_app(tmp_root: str):
    """Build a minimal FastAPI app wired with report_router only.

    get_current_user is overridden so /upload can run without auth.
    settings.FILE_STORAGE_ROOT is monkeypatched to tmp_root.
    """
    app = FastAPI()
    app.include_router(report_router, prefix="/api/v1/reports")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="user", hospital_id="H001", id_card_suffix="123456"
    )
    client = TestClient(app)
    return app, client


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.modules.report.router.settings.FILE_STORAGE_ROOT",
        str(tmp_path),
    )
    app, client = _bootstrap_app(str(tmp_path))
    yield {"app": app, "client": client, "tmp": tmp_path}


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _mock_task():
    """Return a MagicMock standing in for the ReportTask returned by create_task."""
    from datetime import datetime
    t = MagicMock()
    t.id = 42
    t.status = "queued"
    t.created_at = datetime(2025, 1, 1, 0, 0, 0)
    return t


def test_T14_1_small_file_persisted(env):
    """5MB upload succeeds (200) and the file is persisted on disk."""
    size_mb = 5
    payload = b"X" * (size_mb * 1024 * 1024)
    with patch("app.modules.report.router.service.create_task", return_value=_mock_task()) as mock_ct:
        r = env["client"].post(
            "/api/v1/reports/upload",
            files={"file": ("test.pdf", io.BytesIO(payload), "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"] == 42
    assert body["status"] == "queued"
    mock_ct.assert_called_once()
    _, kwargs = mock_ct.call_args
    assert kwargs["file_size"] == size_mb * 1024 * 1024
    file_path = kwargs["file_path"]
    assert os.path.exists(file_path)
    assert os.path.getsize(file_path) == size_mb * 1024 * 1024


def test_T14_2_large_file_rejected_not_persisted(env):
    """25MB upload is rejected (400) and no file remains in storage_dir."""
    size_mb = 25
    payload = b"Y" * (size_mb * 1024 * 1024)
    with patch("app.modules.report.router.service.create_task") as mock_ct:
        r = env["client"].post(
            "/api/v1/reports/upload",
            files={"file": ("big.pdf", io.BytesIO(payload), "application/pdf")},
        )
    assert r.status_code == 400, r.text
    assert "too large" in r.json()["detail"].lower()
    mock_ct.assert_not_called()
    storage_dir = env["tmp"] / "H001" / "reports" / "1"
    if storage_dir.exists():
        assert not list(storage_dir.iterdir())


def test_T14_read_uses_bounded_chunk_size(env):
    """Red-first: router must call file.file.read(CHUNK) — not file.file.read() (whole).

    Invokes upload_report directly with a spy file to observe read() argument.
    Old code: read_calls == [-1] (whole file). Streamed: read_calls[0] == 1MB.
    """
    from app.modules.report.router import upload_report

    class _SpyFile:
        def __init__(self, data):
            self._buf = io.BytesIO(data)
            self.read_calls = []
        def read(self, n=-1):
            self.read_calls.append(n)
            return self._buf.read(n)

    spy = _SpyFile(b"X" * (2 * 1024 * 1024))
    upload = MagicMock()
    upload.filename = "test.pdf"
    upload.file = spy

    user = CurrentUser(user_id=1, role="user", hospital_id="H001", id_card_suffix="123456")
    db = MagicMock()
    with patch("app.modules.report.router.service.create_task", return_value=_mock_task()):
        upload_report(file=upload, db=db, current_user=user)
    assert spy.read_calls, "read() was never called"
    assert spy.read_calls[0] == 1024 * 1024, (
        f"expected first read chunk=1MB, got {spy.read_calls[0]} (whole-file read=-1 means OOM-risk)"
    )


def test_T14_3_boundary_cross_partial_file_removed(env):
    """20.5MB crosses the 20MB limit mid-stream; rejected and partial file removed."""
    size_mb = 20
    half_mb = 512 * 1024
    payload = b"Z" * (size_mb * 1024 * 1024 + half_mb)
    with patch("app.modules.report.router.service.create_task") as mock_ct:
        r = env["client"].post(
            "/api/v1/reports/upload",
            files={"file": ("boundary.pdf", io.BytesIO(payload), "application/pdf")},
        )
    assert r.status_code == 400, r.text
    assert "too large" in r.json()["detail"].lower()
    mock_ct.assert_not_called()
    storage_dir = env["tmp"] / "H001" / "reports" / "1"
    if storage_dir.exists():
        assert not list(storage_dir.iterdir())


def _use_suffix_less_user(app):
    """把 get_current_user 覆盖成无 id_card_suffix 的存量 role='user'。"""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=2, role="user", hospital_id="H001"
    )


def test_list_reports_empty_for_legacy_user_without_suffix(env, db):
    """Critical:存量 role='user' 无 id_card_suffix 时列表必须返回空,绝不泄露全库报告。"""
    from datetime import datetime
    t = ReportTask(id=9001, user_id="123456", original_file_path="/x/0.pdf",
                   original_filename="0.pdf", file_type="pdf", file_size=1)
    db.add(t)
    db.flush()
    db.add(ReportInfo(id=9001, task_id=t.id, user_id="123456", name="张三",
                      created_at=datetime(2026, 1, 1)))
    db.commit()

    with patch("app.modules.report.router.service.list_reports",
               wraps=report_service.list_reports) as mock_list:
        _use_suffix_less_user(env["app"])
        r = env["client"].get("/api/v1/reports")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
        mock_list.assert_not_called()


def test_upload_rejected_for_legacy_user_without_suffix(env):
    """Critical:存量 role='user' 无 id_card_suffix 上传必须 400,不触发 create_task(避免 NOT NULL 500)。"""
    with patch("app.modules.report.router.service.create_task") as mock_ct:
        _use_suffix_less_user(env["app"])
        r = env["client"].post(
            "/api/v1/reports/upload",
            files={"file": ("t.pdf", io.BytesIO(b"X" * 1024), "application/pdf")},
        )
    assert r.status_code == 400, r.text
    assert "后六位" in r.json()["detail"]
    mock_ct.assert_not_called()


def test_list_reports_filters_by_name_when_user_id_set(db):
    """user_id 命中时,双锚定过滤生效:同 user 不同 name 的报告被隔离。"""
    from datetime import datetime
    for i, (uid, nm) in enumerate([
        ("123456", "张三"), ("123456", "张三"), ("123456", "李四"),
    ]):
        t = ReportTask(id=1000 + i, user_id=uid, original_file_path=f"/x/{i}.pdf",
                       original_filename=f"{i}.pdf", file_type="pdf", file_size=1)
        db.add(t)
        db.flush()
        db.add(ReportInfo(id=2000 + i, task_id=t.id, user_id=uid, name=nm,
                          created_at=datetime(2026, 1, i + 1)))
    db.commit()

    items, total = report_service.list_reports(db, "H001", user_id="123456", name="张三")
    assert total == 2
    assert all(r["name"] == "张三" for r in items)

    items, total = report_service.list_reports(db, "H001", user_id="123456", name="李四")
    assert total == 1

    items, total = report_service.list_reports(db, "H001", user_id="123456", name="不存在")
    assert total == 0

    # 不带 name 时保持原行为(仅按 user_id 过滤)
    items, total = report_service.list_reports(db, "H001", user_id="123456")
    assert total == 3

    # 未命中 user_id 时不按 name 过滤(保持向后兼容)
    items, total = report_service.list_reports(db, "H001", user_id=None, name="张三")
    assert total == 3