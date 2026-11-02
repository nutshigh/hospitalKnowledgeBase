from sqlalchemy import String

from app.config import Settings
from app.modules.risk.models import DiseaseHit


def test_szbf_defaults():
    s = Settings(_env_file=None)
    assert s.SZBF_ORG_ID == "133"
    assert s.SZBF_TARGET_HOSPITAL_ID == "133"
    assert s.SZBF_INDICATOR_SOURCE == "merge"
    assert s.SZBF_SYNC_LOOKBACK_MINUTES == 10
    assert s.SZBF_SYNC_BATCH_SIZE == 100
    assert s.SZBF_SOURCE_USER == "readonly"


def test_disease_hit_user_id_is_string():
    assert isinstance(DiseaseHit.__table__.c.user_id.type, String)
    assert DiseaseHit.__table__.c.user_id.type.length == 64
