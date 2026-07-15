import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser
from app.modules.report.batch_router import router as batch_router
from app.modules.report.batch_models import BatchImport, BatchImportFile


def _bootstrap_app(tmp_root: str):
    """Build a FastAPI app that includes only batch_router, with SQLite DB
    wired through a monkeypatched get_hospital_db and a mock current admin user.
    Returns (app, client, engine, SessionLocal)."""
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

    os.environ.setdefault("FILE_STORAGE_ROOT", tmp_root)
    app = FastAPI()
    app.include_router(batch_router, prefix="/api/v1/reports")

    # patch get_hospital_db inside batch_router to use our SQLite session
    real_db_module = __import__("app.modules.report.batch_router", fromlist=["get_hospital_db"])
    fake_store = {"session": None}

    def _fake_get_hospital_db(hospital_id):
        gen_session = SessionLocal()
        fake_store["session"] = gen_session
        try:
            yield gen_session
        finally:
            gen_session.close()

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="admin", hospital_id="H001"
    )
    client = TestClient(app)
    return app, client, engine, SessionLocal, _fake_get_hospital_db, fake_store


@pytest.fixture
def env():
    tmp = tempfile.mkdtemp()
    app, client, engine, SessionLocal, fake_get_db, store = _bootstrap_app(tmp)
    p_db = patch("app.modules.report.batch_router.get_hospital_db", side_effect=fake_get_db)
    p_svc = patch("app.modules.report.batch_service.rabbitmq")
    p_router = patch("app.modules.report.batch_router.rabbitmq")
    p_root = patch("app.modules.report.batch_service.settings.FILE_STORAGE_ROOT", tmp)
    p_max = patch("app.modules.report.batch_service.settings.BATCH_ARCHIVE_MAX_SIZE", 10 * 1024 * 1024 * 100)
    p_root_router = patch("app.modules.report.batch_router.settings", MagicMock(FILE_STORAGE_ROOT=tmp, BATCH_ARCHIVE_MAX_SIZE=10*1024*1024*100))
    svc_mq = p_svc.start()
    svc_mq.publish.side_effect = lambda m: None
    router_mq = p_router.start()
    router_mq.consume_dead.return_value = []
    p_db.start(); p_root.start(); p_max.start(); p_root_router.start()
    yield {
        "app": app, "client": client, "engine": engine,
        "Session": SessionLocal, "store": store, "svc_mq": svc_mq,
        "router_mq": router_mq,
    }
    p_db.stop(); p_svc.stop(); p_router.stop(); p_root.stop()
    p_max.stop(); p_root_router.stop()


def _list_batches(client):
    return client.get("/api/v1/reports/batches")


def test_T11_1_create_then_status_uploading(env):
    r = env["client"].post(
        "/api/v1/reports/batches",
        data={"filename": "import.zip"},
    )
    assert r.status_code == 200, r.text
    bid = r.json()["batch_id"]

    g = env["client"].get(f"/api/v1/reports/batches/{bid}")
    assert g.status_code == 200
    assert g.json()["batch"]["status"] == "uploading"


def test_T11_2_chunk_and_complete_to_extracting(env):
    bid = env["client"].post(
        "/api/v1/reports/batches", data={"filename": "import.zip"}
    ).json()["batch_id"]

    chunks = [b"hello", b"world", b"!!!!"]
    for i, c in enumerate(chunks):
        r = env["client"].post(
            f"/api/v1/reports/batches/{bid}/chunk",
            data={"index": i, "total": 3},
            files={"data": ("part", c, "application/octet-stream")},
        )
        assert r.status_code == 200, r.text

    cr = env["client"].post(
        f"/api/v1/reports/batches/{bid}/complete",
        json={"expected_crc32": None, "expected_total": 3, "expected_size": 15},
    )
    assert cr.status_code == 200, cr.text
    assert cr.json()["status"] == "extracting"

    g = env["client"].get(f"/api/v1/reports/batches/{bid}")
    assert g.json()["batch"]["status"] == "extracting"


def test_T11_2b_complete_crc_mismatch_400(env):
    bid = env["client"].post(
        "/api/v1/reports/batches", data={"filename": "import.zip"}
    ).json()["batch_id"]

    for i in range(3):
        env["client"].post(
            f"/api/v1/reports/batches/{bid}/chunk",
            data={"index": i, "total": 3},
            files={"data": ("p", b"hello", "application/octet-stream")},
        )

    cr = env["client"].post(
        f"/api/v1/reports/batches/{bid}/complete",
        json={"expected_crc32": "deadbeef", "expected_total": 3, "expected_size": 15},
    )
    assert cr.status_code == 400
    assert cr.json()["detail"] == "crc_mismatch"


def test_complete_archive_too_large_400(env):
    """I4: F1 archive_too_large 端到端 + M1 分片清理。"""
    import glob
    from app.config import settings
    with patch.object(settings, "BATCH_ARCHIVE_MAX_SIZE", 100):
        bid = env["client"].post(
            "/api/v1/reports/batches", data={"filename": "big.zip"}
        ).json()["batch_id"]

        chunks = [b"x" * 100, b"y" * 100]  # total 200 bytes > 100
        for i, c in enumerate(chunks):
            r = env["client"].post(
                f"/api/v1/reports/batches/{bid}/chunk",
                data={"index": i, "total": 2},
                files={"data": ("p", c, "application/octet-stream")},
            )
            assert r.status_code == 200, r.text

        cr = env["client"].post(
            f"/api/v1/reports/batches/{bid}/complete",
            json={"expected_crc32": None, "expected_total": 2, "expected_size": 200},
        )
        assert cr.status_code == 400
        assert cr.json()["detail"] == "archive_too_large"

        # batch cancelled
        s = env["Session"]()
        b = s.query(BatchImport).get(bid)
        assert b.status == "cancelled"
        assert b.error_message == "archive_too_large"
        # M1: .partN 分片已被清理
        part_dir = os.path.dirname(b.archive_path)
        parts = glob.glob(os.path.join(part_dir, f"{bid}.part*"))
        assert parts == []
        s.close()


def test_T11_3_progress_with_failing_files(env):
    s = env["Session"]()
    b = BatchImport(id="b1", hospital_id="H001", user_id="1", filename="x.zip",
                    archive_path="/x.zip", status="partial_failed", total=2, failed=1)
    f1 = BatchImportFile(id="f1", batch_id="b1", file_path="/f1.pdf",
                         file_size=1, crc32="abc12345", status="failed", error_message="boom")
    f2 = BatchImportFile(id="f2", batch_id="b1", file_path="/f2.pdf",
                         file_size=1, crc32="def12345", status="interp_ok")
    s.add_all([b, f1, f2]); s.commit(); s.close()

    g = env["client"].get("/api/v1/reports/batches/b1")
    assert g.status_code == 200
    body = g.json()
    assert body["batch"]["status"] == "partial_failed"
    failing_ids = [ff["id"] for ff in body["failing_files"]]
    assert failing_ids == ["f1"]


def test_T11_4_dead_letter(env):
    env["router_mq"].consume_dead.return_value = [
        {"task_type": "parsing", "batch_id": "b1"},
    ]
    r = env["client"].get("/api/v1/reports/batches/b1/dead")
    assert r.status_code == 200
    assert r.json() == {"dead": [{"task_type": "parsing", "batch_id": "b1"}]}
    env["router_mq"].consume_dead.assert_called_with("b1")


def test_T11_5_retry_failed_partial(env):
    s = env["Session"]()
    b = BatchImport(id="rb", hospital_id="H001", user_id="1", filename="x.zip",
                    archive_path="/x.zip", status="partial_failed", total=2, failed=1)
    f = BatchImportFile(id="rf", batch_id="rb", file_path="/pf.pdf",
                        file_size=1, crc32="abc12345", status="failed", error_message="x")
    s.add_all([b, f]); s.commit(); s.close()

    r = env["client"].post("/api/v1/reports/batches/rb/retry", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"requeued": 1}


def test_T11_6_cancel_uploading_then_completed_400(env):
    bid = env["client"].post(
        "/api/v1/reports/batches", data={"filename": "x.zip"}
    ).json()["batch_id"]

    c = env["client"].post(f"/api/v1/reports/batches/{bid}/cancel")
    assert c.status_code == 200, c.text
    assert c.json() == {"cancelled": True}

    # set one to completed and verify cancel is rejected
    s = env["Session"]()
    row = s.query(BatchImport).get(bid)
    row.status = "completed"
    s.commit(); s.close()

    c2 = env["client"].post(f"/api/v1/reports/batches/{bid}/cancel")
    assert c2.status_code == 400


def test_T11_7_non_admin_403(env):
    env["app"].dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=2, role="user", hospital_id="H001"
    )
    r = env["client"].post("/api/v1/reports/batches", data={"filename": "x.zip"})
    assert r.status_code == 403


def test_T11_list_batches_pagination(env):
    s = env["Session"]()
    for i in range(3):
        s.add(BatchImport(id=f"l{i}", hospital_id="H001", user_id="1",
                          filename=f"{i}.zip", archive_path="/x", status="uploading"))
    s.commit(); s.close()

    r = _list_batches(env["client"])
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert "created_at" in body["items"][0]