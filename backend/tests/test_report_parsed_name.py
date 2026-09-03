"""单份上传「归属名与展示名分离」: parsed_name 只做展示, name 保持归属锚定。

归属语义: report_info.name = 登录账号锚定名(如 测试1), 过滤不破;
          report_info.parsed_name = PDF 解析出的真实姓名(如 孙越锋), 仅展示。
"""
from datetime import datetime, date
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation  # noqa: F401


def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_int(
        ReportTask.__table__.c.id,
        ReportInfo.__table__.c.id,
        ReportIndicator.__table__.c.id,
    )
    Base.metadata.create_all(engine)
    _restore(saved)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_task(db, user_id="100001", status="queued", name="测试1"):
    t = ReportTask(
        user_id=user_id, original_file_path="/tmp/孙越锋.pdf",
        original_filename="孙越锋.pdf", file_type="pdf", file_size=10,
        status=status, priority=0,
    )
    db.add(t); db.commit(); db.refresh(t)
    db.add(ReportInfo(task_id=t.id, user_id=user_id, name=name))
    db.commit()
    return t


def _mock_parse_text_pipeline():
    """文本 PDF 解析路径的 mock: 命中 LLM 文本解析, 返回真实姓名孙越锋。"""
    return (
        patch("app.modules.report.service._pdf_has_text", return_value=True),
        patch("app.modules.report.service._extract_pdf_text", return_value="体检文本"),
        patch("app.modules.report.service._parse_text_with_llm", return_value={
            "name": "孙越锋", "gender": "男", "age": 33,
            "report_date": date(2025, 6, 24),
            "indicators": [
                {"item_name": "收缩压", "result": "145", "unit": "mmHg",
                 "ref_low": "90", "ref_high": "140"},
            ],
        }),
        patch("app.modules.report.service.rabbitmq.publish"),
    )


def test_process_task_writes_parsed_name_not_overwrite_anchor(db):
    """单份上传锚定名=测试1 时, 解析后 parsed_name=孙越锋, name 不被覆盖。"""
    from app.modules.report.service import process_task
    t = _make_task(db, name="测试1")
    patchers = _mock_parse_text_pipeline()
    for p in patchers:
        p.start()
    try:
        process_task(db, t.id, "1")
    finally:
        for p in patchers:
            p.stop()
    db.expire_all()
    r = db.query(ReportInfo).filter(ReportInfo.task_id == t.id).first()
    assert r.name == "测试1"          # 归属锚定不变
    assert r.parsed_name == "孙越锋"   # 展示用解析真实姓名


def test_process_task_parsed_name_none_when_missing(db):
    """PDF 未识别出姓名时 parsed_name 保持 NULL, name 回填逻辑不变。"""
    from app.modules.report.service import process_task
    t = _make_task(db, name=None)  # 存量旧数据 name 为空
    p1 = patch("app.modules.report.service._pdf_has_text", return_value=True)
    p2 = patch("app.modules.report.service._extract_pdf_text", return_value="x")
    p3 = patch("app.modules.report.service._parse_text_with_llm", return_value={
        "name": None, "indicators": [],
    })
    p4 = patch("app.modules.report.service.rabbitmq.publish")
    for p in (p1, p2, p3, p4):
        p.start()
    try:
        process_task(db, t.id, "1")
    finally:
        for p in (p1, p2, p3, p4):
            p.stop()
    db.expire_all()
    r = db.query(ReportInfo).filter(ReportInfo.task_id == t.id).first()
    assert r.name is None
    assert r.parsed_name is None


def test_list_reports_display_parsed_name(db):
    """列表展示 name 用 parsed_name or name: 测试1 归属能查到, 但展示孙越锋。"""
    from app.modules.report.service import list_reports
    t = _make_task(db, name="测试1")
    r = db.query(ReportInfo).filter(ReportInfo.task_id == t.id).first()
    r.parsed_name = "孙越锋"
    r.created_at = datetime(2026, 1, 1)
    t.status = "completed"
    db.commit()

    items, total = list_reports(db, "1", user_id="100001", name="测试1")
    assert total == 1
    assert items[0]["name"] == "孙越锋"

    items, total = list_reports(db, "1", user_id="100001", name="测试1")
    assert total == 1  # 归属过滤仍按锚定名命中


def test_list_reports_name_empty_while_parsing(db):
    """解析中(未完成)且 parsed_name 为空: 展示 name 应为空, 不泄露账号锚定名。"""
    from app.modules.report.service import list_reports
    t = _make_task(db, name="测试1")  # status=queued, parsed_name 空
    r = db.query(ReportInfo).filter(ReportInfo.task_id == t.id).first()
    r.created_at = datetime(2026, 1, 1)
    db.commit()

    items, total = list_reports(db, "1", user_id="100001", name="测试1")
    assert total == 1
    assert items[0]["name"] is None


def test_list_reports_fallback_completed_legacy_name(db):
    """任务已完成但 parsed_name 空(解析未抽出姓名): 回退展示 name(归属锚定名)。"""
    from app.modules.report.service import list_reports
    t = _make_task(db, name="测试1")
    t.status = "completed"
    r = db.query(ReportInfo).filter(ReportInfo.task_id == t.id).first()
    r.created_at = datetime(2026, 1, 1)
    db.commit()

    items, total = list_reports(db, "1", user_id="100001", name="测试1")
    assert total == 1
    assert items[0]["name"] == "测试1"


def _patch_detail_services(client_deps=(), *, task_status="completed", parsed_name=None, name="测试1"):
    """detail router 依赖服务的最小 stub 集(含 get_task_status)。"""
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.dependencies import get_current_user, CurrentUser
    from app.modules.report.router import router as report_router

    app = FastAPI()
    app.include_router(report_router, prefix="/api/v1/reports")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="user", hospital_id="1", id_card_suffix="100001", name="测试1"
    )
    client = TestClient(app)

    stub = SimpleNamespace(
        id=1, task_id=1, name=name, parsed_name=parsed_name,
        gender=None, age=None, report_date=None, check_type=None,
        unit_name=None, created_at=datetime(2026, 1, 1),
    )
    task_stub = SimpleNamespace(status=task_status)

    patches = [
        patch("app.modules.report.router.service.get_report_detail",
              return_value=stub),
        patch("app.modules.report.router.service.get_report_indicators",
              return_value=[]),
        patch("app.modules.report.router.service.get_task_status",
              return_value=task_stub),
    ]
    return client, patches


def test_get_report_detail_display_parsed_name(db):
    """详情展示 name 用 parsed_name or name(router 映射)。"""
    client, patches = _patch_detail_services(parsed_name="孙越锋")
    for p in patches:
        p.start()
    try:
        r = client.get("/api/v1/reports/1")
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "孙越锋"


def test_get_report_detail_display_fallback_name(db):
    """parsed_name 为空且任务已完成:详情展示回退到锚定 name(旧数据兼容)。"""
    client, patches = _patch_detail_services(parsed_name=None, task_status="completed")
    for p in patches:
        p.start()
    try:
        r = client.get("/api/v1/reports/1")
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "测试1"


def test_get_report_detail_name_empty_while_parsing(db):
    """解析中(parsed_name 空 + 任务 parsing):详情展示 name 应为空, 不泄露账号锚定名。"""
    client, patches = _patch_detail_services(parsed_name=None, task_status="parsing")
    for p in patches:
        p.start()
    try:
        r = client.get("/api/v1/reports/1")
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 200, r.text
    assert r.json()["name"] is None
