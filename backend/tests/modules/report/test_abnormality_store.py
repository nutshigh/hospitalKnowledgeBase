"""结论异常落库(_store_abnormalities)去重护栏(2026-09-16)。

背景: H003-30(陈磊) 结论条目整组成对入库(27043-27053 / 27054-27064 两批,
建议文案差异 = 两次独立 LLM 抽取) —— 并发 backfill 竞态(两个 worker / watchdog
重投 + 正常链), 而 autoflush=False 下循环内与入口的 COUNT 都看不到未提交行。
修法: 入口 SELECT ... FOR UPDATE 串行化(MySQL) + 本调用内 _seen_names 去重。

运行: cd backend && .venv/bin/python -m pytest tests/modules/report/test_abnormality_store.py -q
"""
from sqlalchemy import Integer, create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
# 触发所有表注册到 Base.metadata
from app.modules.report.models import ReportInfo, ReportIndicator  # noqa: F401
from app.modules.interpretation.models import (  # noqa: F401
    IndicatorJudgment,
    ReportInterpretation,
)
from app.modules.report.service import _store_abnormalities


def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_int(
        ReportInfo.__table__.c.id,
        ReportIndicator.__table__.c.id,
        ReportInterpretation.__table__.c.id,
        ReportInterpretation.__table__.c.report_id,
        IndicatorJudgment.__table__.c.id,
        IndicatorJudgment.__table__.c.interpretation_id,
        IndicatorJudgment.__table__.c.indicator_id,
    )
    Base.metadata.create_all(engine)
    _restore(saved)
    db = sessionmaker(bind=engine, autoflush=False)()  # 与生产 Session 同配置
    # disease_mapping 非 ORM 模型(DDL 建表), 结论去重链会 SELECT 它
    db.execute(text("CREATE TABLE disease_mapping ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "disease_name TEXT, item_name_standard TEXT)"))
    return db


def _seed(db):
    db.add(ReportInfo(id=1, task_id=1, user_id="100006", name="测试6"))
    db.add(ReportInterpretation(id=7, report_id=1, status="completed"))
    db.flush()


def _conclusion_rows(db, iid):
    return db.execute(text(
        "SELECT COUNT(*) FROM indicator_judgment ij JOIN report_indicator ri "
        "ON ij.indicator_id = ri.id "
        "WHERE ij.interpretation_id = :iid AND ri.raw_text IS NOT NULL"),
        {"iid": iid}).scalar()


def test_same_name_items_stored_once():
    """同调用内同名条目(双 pass 残留)只落一行 —— 陈磊成对入库根因。"""
    db = _make_session()
    _seed(db)
    _store_abnormalities(db, 1, 7, [
        {"item_name": "ST段改变", "suggestion": "心内科就诊，进一步检查评估"},
        {"item_name": "高脂血症", "suggestion": "建议内分泌科就诊"},
        {"item_name": "ST段改变", "suggestion": "建议心内科就诊，进一步检查评估。"},
        {"item_name": "高脂血症", "suggestion": "建议内分泌科就诊。"},
    ])
    db.commit()
    assert _conclusion_rows(db, 7) == 2
    names = db.execute(text(
        "SELECT DISTINCT ij.item_name FROM indicator_judgment ij "
        "JOIN report_indicator ri ON ij.indicator_id = ri.id "
        "WHERE ij.interpretation_id = 7")).fetchall()
    assert sorted(n for (n,) in names) == ["ST段改变", "高脂血症"]
    # 保留首次出现的文案(未被第二次覆盖)
    sug = db.execute(text(
        "SELECT ij.suggestion FROM indicator_judgment ij WHERE ij.item_name = 'ST段改变'"
    )).scalar()
    assert sug == "心内科就诊，进一步检查评估"


def test_second_call_in_same_transaction_skips():
    """同事务内二次调用(并发等价情形)不追加 —— 入口 existing 检查需看到已插行。"""
    db = _make_session()
    _seed(db)
    _store_abnormalities(db, 1, 7, [{"item_name": "胆囊壁毛糙", "suggestion": "随访"}])
    db.flush()
    _store_abnormalities(db, 1, 7, [{"item_name": "胆囊结石", "suggestion": "随访"}])
    db.commit()
    assert _conclusion_rows(db, 7) == 1


def test_empty_items_noop():
    db = _make_session()
    _seed(db)
    _store_abnormalities(db, 1, 7, [])
    db.commit()
    assert _conclusion_rows(db, 7) == 0
