from sqlalchemy import BigInteger, Column, Date, DateTime, Integer, JSON, String, func

from app.models.base import Base


class DiseaseRule(Base):
    """异常指标→病种 严格AND组合规则(rule_code 唯一, source=CENTRAL/LOCAL)。"""

    __tablename__ = "disease_rule"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_code = Column(String(50), nullable=False, unique=True)
    disease_name = Column(String(100), nullable=False)
    disease_category = Column(String(20), nullable=False, default="CHRONIC")  # CHRONIC|MAJOR|OTHER
    disease_class = Column(String(100), nullable=True)
    member_items = Column(JSON, nullable=False)  # [{"name": 标准名, "min_level": YELLOW|RED, "deviation": 偏高|偏低|null}]
    source = Column(String(20), nullable=False, default="LOCAL")  # CENTRAL|LOCAL
    enabled = Column(Integer, nullable=False, default=1)
    sort_code = Column(Integer, default=200)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class DiseaseHit(Base):
    """报告粒度病种命中固化(uk: report_id+disease_name;冗余 user_id/unit_name/report_date)。"""

    __tablename__ = "disease_hit"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, nullable=False)
    interpretation_id = Column(BigInteger, nullable=True)
    user_id = Column(BigInteger, nullable=False)
    unit_name = Column(String(100), nullable=True)
    report_date = Column(Date, nullable=True)
    disease_name = Column(String(100), nullable=False)
    disease_category = Column(String(20), nullable=False, default="CHRONIC")
    disease_class = Column(String(100), nullable=True)
    hit_type = Column(String(20), nullable=False, default="single")  # single|combo
    mapping_id = Column(BigInteger, nullable=True)
    rule_id = Column(BigInteger, nullable=True)
    hit_items = Column(JSON, nullable=True)  # ["收缩压（SBP）","舒张压（DBP）"]
    created_at = Column(DateTime, server_default=func.now())
