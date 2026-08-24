from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.risk.models import DiseaseRule, DiseaseHit  # noqa: F401
from app.modules.risk.seed import CENTRAL_RULES, CENTRAL_MAPPINGS, sync_central

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


def _fresh_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    # 重建为 sqlite 自增主键表(BigInteger 在 sqlite 不自增)
    s.execute(text("DROP TABLE disease_rule"))
    s.execute(text("DROP TABLE disease_hit"))
    s.execute(text(_RULE_DDL))
    s.execute(text(_HIT_DDL))
    s.execute(text(_MAPPING_DDL))
    s.commit()
    return s


def test_sync_central_idempotent():
    s = _fresh_session()
    sync_central(s)
    n1 = s.execute(text("SELECT COUNT(*) FROM disease_rule")).scalar()
    m1 = s.execute(text("SELECT COUNT(*) FROM disease_mapping")).scalar()
    sync_central(s)
    n2 = s.execute(text("SELECT COUNT(*) FROM disease_rule")).scalar()
    m2 = s.execute(text("SELECT COUNT(*) FROM disease_mapping")).scalar()
    assert n1 == n2 == len(CENTRAL_RULES)
    assert m1 == m2 == len(CENTRAL_MAPPINGS)
    row = s.execute(text(
        "SELECT match_level, match_deviation FROM disease_mapping"
        " WHERE item_name_standard='体重指数'")).fetchone()
    assert row == ("RED", "偏高")


def test_sync_updates_existing_central():
    s = _fresh_session()
    s.execute(text(
        "INSERT INTO disease_mapping (id, item_name_standard, disease_name,"
        " disease_category, source) VALUES (1, '体重指数', '超重', 'OTHER', 'CENTRAL')"))
    s.commit()
    sync_central(s)
    row = s.execute(text(
        "SELECT disease_name, match_level FROM disease_mapping"
        " WHERE item_name_standard='体重指数'")).fetchone()
    assert row == ("肥胖症", "RED")
