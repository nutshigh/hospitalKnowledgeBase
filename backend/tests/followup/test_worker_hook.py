"""interpretation worker 成功分支应调用 try_generate_followup;其失败不影响解读完成。"""
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportInfo  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation  # noqa: F401
from app.modules.followup.models import (  # noqa: F401
    Followup, FollowupQuestion, UserNotification,
    FollowupTemplate, FollowupTemplateQuestion,
)


def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


def _gen(session):
    yield session


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_int(ReportInfo.__table__.c.id,
                      ReportInterpretation.__table__.c.id,
                      ReportInterpretation.__table__.c.report_id)
    Base.metadata.create_all(engine)
    _restore(saved)
    s = sessionmaker(bind=engine)()
    Mq = MagicMock()
    p_getdb = patch("app.modules.interpretation.worker.get_hospital_db", lambda hid: _gen(s))
    p_win = patch("app.modules.interpretation.worker.is_bulk_window_now", return_value=True)
    p_agent = patch("app.modules.interpretation.worker.run_interpretation_agent")
    p_cmp = patch("app.modules.user_profile.service.try_generate_comparison_summary")
    p_batch = patch("app.modules.interpretation.worker.BatchService")
    p_rabbit = patch("app.modules.interpretation.worker.rabbitmq", Mq)
    p_followup = patch("app.modules.followup.service.try_generate_followup")
    agent_mock = p_agent.start()
    followup_mock = p_followup.start()
    for p in (p_getdb, p_win, p_cmp, p_batch, p_rabbit):
        p.start()
    try:
        yield s, agent_mock, followup_mock
    finally:
        for p in (p_getdb, p_win, p_agent, p_cmp, p_batch, p_rabbit, p_followup):
            p.stop()
        s.close()


def _make_report(db):
    r = ReportInfo(user_id="123456", name="张三")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_success_calls_followup_hook(env):
    s, agent_mock, followup_mock = env
    r = _make_report(s)
    agent_mock.return_value = {}
    from app.modules.interpretation.worker import handle_interpretation_task
    msg = {"_routing_key": "interpretation.normal",
           "payload": {"report_id": r.id, "hospital_id": "H001"}}
    handle_interpretation_task(msg)
    followup_mock.assert_called_once()
    args, _ = followup_mock.call_args
    assert args[1] == r.id


def test_followup_failure_does_not_break(env):
    s, agent_mock, followup_mock = env
    r = _make_report(s)
    agent_mock.return_value = {}
    followup_mock.side_effect = RuntimeError("boom")
    from app.modules.interpretation.worker import handle_interpretation_task
    msg = {"_routing_key": "interpretation.normal",
           "payload": {"report_id": r.id, "hospital_id": "H001"}}
    handle_interpretation_task(msg)  # 不抛
