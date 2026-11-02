from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.interpretation import models as interp_models  # noqa: F401
from app.modules.report import service
from app.modules.report.models import ReportInfo


def test_list_reports_hides_szbf():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(ReportInfo(id=1, user_id="u1", name="张三", parsed_name="张三",
                      check_type="SZBF", report_date=None))
    db.add(ReportInfo(id=2, user_id="u2", name="李四", parsed_name="李四",
                      check_type=None, report_date=None))
    db.commit()
    items, total = service.list_reports(db, "133")
    assert total == 1
    assert items[0]["name"] == "李四"
