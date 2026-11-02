from datetime import date

import pytest
from sqlalchemy import create_engine, text

from app.modules.szbf.source import SourceReader, _chunked


@pytest.fixture()
def engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE ba_user (
              user_id BIGINT, real_name TEXT, user_no TEXT,
              user_nick TEXT, user_name TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE ba_user_basic (
              user_id BIGINT, org_id BIGINT, sex TEXT, birthday TEXT, age TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE ba_organization (
              org_id BIGINT, org_code TEXT, org_name TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE szbf_exam_detail (
              id BIGINT, user_id BIGINT, name TEXT, sex TEXT, birthday TEXT,
              exam_code TEXT, exam_date TEXT, year TEXT, item_name TEXT,
              result TEXT, item_unit TEXT, reference_range TEXT,
              exception_flag TEXT, exception_tips TEXT, dept_name TEXT,
              item_code TEXT, is_combine TEXT, delete_flag TEXT, update_time TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE szbf_exam_conclusion_record (
              user_id BIGINT, con_suggest TEXT, conclusion TEXT, update_time TEXT
            )
        """))
        conn.execute(text("INSERT INTO ba_organization VALUES (133,'132','北京总站')"))
        conn.execute(text("INSERT INTO ba_user VALUES (1,'张三','1',NULL,NULL)"))
        conn.execute(text("INSERT INTO ba_user_basic VALUES (1,133,'1','1990-01-01','36')"))
        conn.execute(text("""
            INSERT INTO szbf_exam_detail VALUES
            (1,1,'张三','1','1990-01-01','E1','2026-10-01','2026',
             '总胆固醇(CHOL)','5.32','mmol/L','<5.18','1','偏高','检验科',
             'CHOL','0','0','2026-10-02 10:00:00')
        """))
        conn.execute(text("""
            INSERT INTO szbf_exam_conclusion_record VALUES
            (1,'1.脂肪肝','一、结论','2026-10-02 10:00:00')
        """))
    return engine


def test_fetch_conclusion_only_users_and_station_name(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ba_user VALUES (2,'李四','2',NULL,NULL)"))
        conn.execute(text("INSERT INTO ba_user_basic VALUES (2,133,'2','1990-01-01','36')"))
        conn.execute(text("""
            INSERT INTO szbf_exam_conclusion_record VALUES
            (2,'脂肪肝','一、结论','2026-10-02 10:00:00')
        """))
    reader = SourceReader(engine, "133")
    assert reader.station_name() == "北京总站"
    assert reader.fetch_conclusion_only_users() == [("2", "李四")]


def test_fetch_exams(engine):
    reader = SourceReader(engine, "133")
    exams = reader.fetch_exams()
    assert len(exams) == 1
    assert exams[0].exam_code == "E1"
    assert exams[0].org_name == "北京总站"
    assert exams[0].details[0]["item_name"] == "总胆固醇(CHOL)"
    assert exams[0].con_suggest == "1.脂肪肝"


def test_changed_keys(engine):
    reader = SourceReader(engine, "133")
    assert reader.fetch_changed_keys("2026-10-02 09:00:00") == [("1", "E1")]
    assert reader.fetch_changed_conclusion_users("2026-10-02 09:00:00") == ["1"]


def test_fetch_exams_changed_since_refetches_whole_exam(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO szbf_exam_detail VALUES
            (2,1,'张三','1','1990-01-01','E1','2026-10-01','2026',
             '甘油三酯(TG)','1.20','mmol/L','<1.70','0',NULL,'检验科',
             'TG','0','0','2026-09-01 10:00:00')
        """))
    reader = SourceReader(engine, "133")
    exams = reader.fetch_exams(changed_since="2026-10-02 09:00:00")
    assert len(exams) == 1
    assert len(exams[0].details) == 2
    assert {d["item_name"] for d in exams[0].details} == {
        "总胆固醇(CHOL)", "甘油三酯(TG)"
    }


def test_fetch_exams_by_keys_and_fetch_exam(engine):
    reader = SourceReader(engine, "133")
    exams = reader.fetch_exams_by_keys([("1", "E1")])
    assert [e.exam_code for e in exams] == ["E1"]
    assert reader.fetch_exam("1", "E1").user_id == "1"
    assert reader.fetch_exam("1", "MISSING") is None
    assert reader.fetch_exams_by_keys([]) == []


def test_fetch_latest_exam_for_user(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO szbf_exam_detail VALUES
            (3,1,'张三','1','1990-01-01','E2','2026-11-01','2026',
             '血糖(GLU)','5.10','mmol/L','3.90-6.10','0',NULL,'检验科',
             'GLU','0','0','2026-11-02 10:00:00')
        """))
    reader = SourceReader(engine, "133")
    exam = reader.fetch_latest_exam_for_user("1")
    assert exam is not None
    assert exam.exam_code == "E2"
    assert exam.exam_date == date(2026, 11, 1)
    assert reader.fetch_latest_exam_for_user("999") is None


def test_conclusions_attach_only_to_latest_exam(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO szbf_exam_detail VALUES
            (4,1,'张三','1','1990-01-01','E0','2025-10-01','2025',
             '尿酸(UA)','420','umol/L','208-428','0',NULL,'检验科',
             'UA','0','0','2025-10-02 10:00:00')
        """))
    reader = SourceReader(engine, "133")
    exams = {e.exam_code: e for e in reader.fetch_exams()}
    assert exams["E1"].con_suggest == "1.脂肪肝"
    assert exams["E1"].conclusion == "一、结论"
    assert exams["E0"].con_suggest is None
    assert exams["E0"].conclusion is None
    assert reader.fetch_exam("1", "E0").con_suggest is None


def test_fetch_exams_changed_since_excludes_unchanged_exam(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO szbf_exam_detail VALUES
            (5,1,'张三','1','1990-01-01','E0','2025-10-01','2025',
             '尿酸(UA)','420','umol/L','208-428','0',NULL,'检验科',
             'UA','0','0','2026-09-01 10:00:00')
        """))
    reader = SourceReader(engine, "133")
    exams = reader.fetch_exams(changed_since="2026-10-02 09:00:00")
    assert [e.exam_code for e in exams] == ["E1"]
    assert {d["item_name"] for d in exams[0].details} == {"总胆固醇(CHOL)"}


def test_fetch_exams_filters_other_org_category_and_deleted_rows(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ba_user VALUES (2,'李四','2',NULL,NULL)"))
        conn.execute(text("INSERT INTO ba_user_basic VALUES (2,200,'2','1990-01-01','36')"))
        conn.execute(text("""
            INSERT INTO szbf_exam_detail VALUES
            (6,2,'李四','2','1990-01-01','X1','2026-10-01','2026',
             '血糖(GLU)','9.99','mmol/L','3.90-6.10','1','偏高','检验科',
             'GLU','0','0','2026-10-02 10:00:00'),
            (7,1,'张三','1','1990-01-01','E1','2026-10-01','2026',
             '体检大类','','','','0',NULL,'','','1','0','2026-10-02 10:00:00'),
            (8,1,'张三','1','1990-01-01','E1','2026-10-01','2026',
             '已删除项','1','','','0',NULL,'','','0','1','2026-10-02 10:00:00')
        """))
    reader = SourceReader(engine, "133")
    exams = reader.fetch_exams()
    assert len(exams) == 1
    assert exams[0].user_id == "1"
    assert exams[0].exam_code == "E1"
    assert [d["item_name"] for d in exams[0].details] == ["总胆固醇(CHOL)"]


def test_fetch_exams_by_keys_dedupes_duplicate_keys(engine):
    reader = SourceReader(engine, "133")
    exams = reader.fetch_exams_by_keys([("1", "E1"), ("1", "E1"), (1, "E1")])
    assert len(exams) == 1
    assert len(exams[0].details) == 1


def test_chunked_splits_batches():
    assert [len(batch) for batch in _chunked(list(range(205)), 200)] == [200, 5]
    assert list(_chunked([], 200)) == []
