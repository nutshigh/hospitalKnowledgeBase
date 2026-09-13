from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.risk.models import DiseaseRule, DiseaseHit  # noqa: F401


def test_risk_tables_create():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    s.execute(text(
        "INSERT INTO disease_rule (id, rule_code, disease_name, disease_category, member_items, source, enabled, sort_code)"
        " VALUES (1, 'R1', '高血压', 'CHRONIC',"
        " '[{\"name\": \"收缩压（SBP）\", \"min_level\": \"YELLOW\", \"deviation\": \"偏高\"}]', 'CENTRAL', 1, 200)"
    ))
    s.execute(text(
        "INSERT INTO disease_hit (id, report_id, user_id, disease_name, disease_category, hit_type, hit_items)"
        " VALUES (1, 1, 10, '高血压', 'CHRONIC', 'single', '[\"收缩压（SBP）\"]')"
    ))
    s.commit()
    assert s.execute(text("SELECT COUNT(*) FROM disease_rule")).scalar() == 1
    assert s.execute(text("SELECT COUNT(*) FROM disease_hit")).scalar() == 1
