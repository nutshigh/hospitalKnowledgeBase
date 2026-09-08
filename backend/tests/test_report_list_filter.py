"""doctor/admin 全量报告列表应排除 failed 与空壳残留行(白行来源);患者路径不受影响。"""
from datetime import date

import pytest
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportTask, ReportInfo  # noqa: F401
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
    saved = _swap_int(ReportTask.__table__.c.id, ReportInfo.__table__.c.id)
    Base.metadata.create_all(engine)
    _restore(saved)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed(db):
    def task(status):
        t = ReportTask(user_id="2", original_file_path="/tmp/x.pdf",
                       original_filename="x.pdf", file_type="pdf",
                       file_size=1, status=status)
        db.add(t)
        db.flush()
        return t.id

    # (a) 失败任务 + 全空
    tid = task("failed")
    db.add(ReportInfo(task_id=tid, user_id="2"))
    # (b) completed 但全空壳
    tid = task("completed")
    db.add(ReportInfo(task_id=tid, user_id="2"))
    # (c) 正常行
    tid = task("completed")
    db.add(ReportInfo(task_id=tid, user_id="011234", name="张三",
                      parsed_name="张子冉", gender="男", age=29,
                      report_date=date(2025, 6, 20)))
    # (d) 无 task 但有数据(存量,应保留)
    db.add(ReportInfo(user_id="2", name="李四", gender="女",
                      report_date=date(2025, 1, 1)))
    db.commit()


def test_doctor_list_hides_failed_and_blank_shells(db):
    from app.modules.report.service import list_reports
    _seed(db)
    items, total = list_reports(db, "H001", None, None, 1, 20)
    assert total == 2  # (c) + (d)
    names = {i["name"] for i in items}
    assert "张子冉" in names and "李四" in names


def test_user_anchor_list_unaffected(db):
    from app.modules.report.service import list_reports
    _seed(db)
    items, total = list_reports(db, "H001", "011234", "张三", 1, 20)
    assert total == 1
    assert items[0]["name"] == "张子冉"
