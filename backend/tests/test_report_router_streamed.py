import os
import io
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser
from app.modules.report.router import router as report_router
from app.modules.report import service as report_service


def _bootstrap_app(tmp_root: str):
    """Build a minimal FastAPI app wired with report_router only.

    get_current_user is overridden so /upload can run without auth.
    settings.FILE_STORAGE_ROOT is monkeypatched to tmp_root.
    """
    app = FastAPI()
    app.include_router(report_router, prefix="/api/v1/reports")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="user", hospital_id="H001"
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

    user = CurrentUser(user_id=1, role="user", hospital_id="H001")
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