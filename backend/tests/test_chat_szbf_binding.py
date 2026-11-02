from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(element, compiler, **kw):
    return compiler.visit_INTEGER(element, **kw)

from app.core.dependencies import CurrentUser, get_current_user
from app.models.base import Base
from app.modules.chat import models as chat_models  # noqa: F401
from app.modules.chat.models import ChatMessage, ChatSession
from app.modules.chat.router import router as chat_router
from app.modules.report import models as report_models  # noqa: F401
from app.modules.report.models import ReportInfo


def _seed_report(db, report_id, user_id="u1", name="张三", check_type=None):
    db.add(ReportInfo(
        id=report_id, user_id=user_id, name=name, parsed_name=name,
        check_type=check_type, report_date=None,
    ))
    db.commit()


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    holder = {}

    def fake_get_hospital_db(hospital_id):
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1/chat")

    def as_user():
        return CurrentUser(
            user_id=1, role="user", hospital_id="H001",
            id_card_suffix="u1", name="张三",
        )

    app.dependency_overrides[get_current_user] = as_user
    holder["app"] = app
    holder["as_user"] = as_user
    patch_obj = patch("app.modules.chat.router.get_hospital_db", fake_get_hospital_db)
    patch_obj.start()
    try:
        yield TestClient(app), Session, holder
    finally:
        patch_obj.stop()
        app.dependency_overrides.clear()


def test_user_cannot_bind_szbf_report(env):
    client, Session, _ = env
    with Session() as db:
        _seed_report(db, 1, check_type="SZBF")

    resp = client.post("/api/v1/chat/sessions", json={"report_id": 1})

    assert resp.status_code == 404, resp.text


def test_user_cannot_bind_another_users_report(env):
    client, Session, _ = env
    with Session() as db:
        _seed_report(db, 2, user_id="u2", name="李四")

    resp = client.post("/api/v1/chat/sessions", json={"report_id": 2})

    assert resp.status_code == 404, resp.text


def test_user_can_bind_own_report(env):
    client, Session, _ = env
    with Session() as db:
        _seed_report(db, 3)

    resp = client.post("/api/v1/chat/sessions", json={"report_id": 3})

    assert resp.status_code == 200, resp.text
    assert resp.json()["report_id"] == 3


def test_user_cannot_bind_missing_report(env):
    client, Session, _ = env

    resp = client.post("/api/v1/chat/sessions", json={"report_id": 999})

    assert resp.status_code == 404, resp.text


def test_update_session_revalidates_binding(env):
    client, Session, _ = env
    with Session() as db:
        _seed_report(db, 1, check_type="SZBF")
        _seed_report(db, 2, user_id="u2", name="李四")
        _seed_report(db, 3)
    created = client.post("/api/v1/chat/sessions", json={"report_id": 3})
    session_id = created.json()["id"]

    szbf = client.patch(
        f"/api/v1/chat/sessions/{session_id}", json={"report_id": 1}
    )
    other = client.patch(
        f"/api/v1/chat/sessions/{session_id}", json={"report_id": 2}
    )
    own = client.patch(
        f"/api/v1/chat/sessions/{session_id}", json={"report_id": 3}
    )

    assert szbf.status_code == 404, szbf.text
    assert other.status_code == 404, other.text
    assert own.status_code == 200, own.text
    assert own.json()["report_id"] == 3


@pytest.mark.parametrize("role", ["doctor", "admin"])
def test_doctor_and_admin_can_bind_normal_report_but_not_szbf(env, role):
    client, Session, holder = env
    with Session() as db:
        _seed_report(db, 1, check_type="SZBF")
        _seed_report(db, 3, user_id="u2", name="李四")

    holder["app"].dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=99, role=role, hospital_id="H001"
    )

    allowed = client.post("/api/v1/chat/sessions", json={"report_id": 3})
    rejected = client.post("/api/v1/chat/sessions", json={"report_id": 1})

    assert allowed.status_code == 200, allowed.text
    assert rejected.status_code == 404, rejected.text


def test_existing_session_bound_to_szbf_rejects_send_message(env):
    client, Session, _ = env
    with Session() as db:
        _seed_report(db, 1, check_type="SZBF")
        session = ChatSession(
            user_id="u1", name="张三", hospital_id="H001", report_id=1,
        )
        db.add(session)
        db.commit()
        session_id = session.id

    resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/messages", json={"content": "hi"}
    )

    assert resp.status_code == 404, resp.text
    with Session() as db:
        assert db.query(ChatMessage).count() == 0


def test_existing_session_bound_to_other_users_report_rejects_send_message(env):
    client, Session, _ = env
    with Session() as db:
        _seed_report(db, 2, user_id="u2", name="李四")
        session = ChatSession(
            user_id="u1", name="张三", hospital_id="H001", report_id=2,
        )
        db.add(session)
        db.commit()
        session_id = session.id

    resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/messages", json={"content": "hi"}
    )

    assert resp.status_code == 404, resp.text
