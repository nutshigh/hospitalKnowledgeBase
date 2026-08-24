from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.risk.models import DiseaseRule, DiseaseHit  # noqa: F401
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment  # noqa: F401
from app.modules.risk import worker as risk_worker

_RULE_DDL = """
CREATE TABLE disease_rule (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_code VARCHAR(50) NOT NULL UNIQUE,
  disease_name VARCHAR(100) NOT NULL,
  disease_category VARCHAR(20) DEFAULT 'CHRONIC',
  disease_class VARCHAR(100),
  member_items TEXT,
  source VARCHAR(20) DEFAULT 'LOCAL',
  enabled TINYINT DEFAULT 1,
  sort_code INT DEFAULT 200,
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  update_time DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
_HIT_DDL = """
CREATE TABLE disease_hit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id BIGINT NOT NULL,
  interpretation_id BIGINT,
  user_id BIGINT NOT NULL,
  unit_name VARCHAR(100),
  report_date DATE,
  disease_name VARCHAR(100) NOT NULL,
  disease_category VARCHAR(20) DEFAULT 'CHRONIC',
  disease_class VARCHAR(100),
  hit_type VARCHAR(20) DEFAULT 'single',
  mapping_id BIGINT,
  rule_id BIGINT,
  hit_items TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (report_id, disease_name)
)
"""
_MAPPING_DDL = """
CREATE TABLE disease_mapping (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_name_standard VARCHAR(200) NOT NULL,
  item_name VARCHAR(200),
  disease_name VARCHAR(200) NOT NULL,
  disease_category VARCHAR(20) DEFAULT 'OTHER',
  disease_class VARCHAR(100),
  sort_code INT DEFAULT 200,
  enabled TINYINT DEFAULT 1,
  source VARCHAR(20) DEFAULT 'LOCAL',
  match_level VARCHAR(10) DEFAULT 'YELLOW',
  match_deviation VARCHAR(10),
  UNIQUE (item_name_standard)
)
"""


def _seed(s):
    s.execute(text(
        "INSERT INTO report_info (id, user_id, name, unit_name, report_date, created_at)"
        " VALUES (1, 10, '陈美杉', '北京友谊医院', '2025-06-13', CURRENT_TIMESTAMP)"))
    s.execute(text(
        "INSERT INTO report_indicator (id, report_id, item_name, item_name_standard)"
        " VALUES (1, 1, '血肌酸激酶', '肌酸激酶（CK）')"))
    s.execute(text(
        "INSERT INTO report_indicator (id, report_id, item_name, item_name_standard)"
        " VALUES (2, 1, '收缩压', '收缩压（SBP）')"))
    s.execute(text(
        "INSERT INTO report_indicator (id, report_id, item_name, item_name_standard)"
        " VALUES (3, 1, '舒张压', '舒张压（DBP）')"))
    s.execute(text(
        "INSERT INTO report_interpretation (id, report_id, status, red_count, yellow_count, green_count, retry_count, created_at)"
        " VALUES (1, 1, 'completed', 0, 0, 0, 0, CURRENT_TIMESTAMP)"))
    s.execute(text(
        "INSERT INTO indicator_judgment (id, interpretation_id, indicator_id, item_name,"
        " color_level, deviation, source) VALUES (1, 1, 1, '肌酸激酶（CK）', 'yellow', '偏高', 'indicator')"))
    s.execute(text(
        "INSERT INTO indicator_judgment (id, interpretation_id, indicator_id, item_name,"
        " color_level, deviation, source) VALUES (2, 1, 2, '收缩压（SBP）', 'yellow', '偏高', 'indicator')"))
    s.execute(text(
        "INSERT INTO indicator_judgment (id, interpretation_id, indicator_id, item_name,"
        " color_level, deviation, source) VALUES (3, 1, 3, '舒张压（DBP）', 'yellow', '偏低', 'indicator')"))
    s.execute(text(
        "INSERT INTO disease_mapping (item_name_standard, disease_name,"
        " disease_category, source, enabled, match_level, match_deviation)"
        " VALUES ('肌酸激酶（CK）', '心肌损伤(疑似)', 'MAJOR', 'CENTRAL', 1, 'YELLOW', '偏高')"))
    s.execute(text(
        "INSERT INTO disease_rule (rule_code, disease_name, disease_category,"
        " member_items, source, enabled)"
        " VALUES ('C-HT', '高血压', 'CHRONIC',"
        " '[{\"name\": \"收缩压（SBP）\", \"min_level\": \"YELLOW\", \"deviation\": \"偏高\"},"
        " {\"name\": \"舒张压（DBP）\", \"min_level\": \"YELLOW\", \"deviation\": \"偏高\"}]',"
        " 'CENTRAL', 1)"))
    s.commit()


def test_compute_and_store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    s.execute(text("DROP TABLE disease_rule"))
    s.execute(text("DROP TABLE disease_hit"))
    s.execute(text(_RULE_DDL))
    s.execute(text(_HIT_DDL))
    s.execute(text(_MAPPING_DDL))
    s.commit()
    _seed(s)
    n = risk_worker.compute_and_store(s, 1)
    # 肌酸激酶命中 1 条; 高血压组合因舒张压方向(偏低)不满足 → 0 条
    assert n == 1
    rows = s.execute(text(
        "SELECT disease_name, hit_type, hit_items FROM disease_hit ORDER BY disease_name"
    )).all()
    assert len(rows) == 1
    assert rows[0][0] == "心肌损伤(疑似)"
    assert rows[0][1] == "single"
    # 幂等: 重复计算不产生重复行
    risk_worker.compute_and_store(s, 1)
    assert s.execute(text("SELECT COUNT(*) FROM disease_hit")).scalar() == 1
