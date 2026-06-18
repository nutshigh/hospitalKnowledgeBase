from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from typing import Dict, Generator, List

from app.config import settings

DATABASE_URL_TEMPLATE = (
    f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
    f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}"
)

_engines: Dict[str, "Engine"] = {}
_SessionLocals: Dict[str, sessionmaker] = {}


def _build_engine(db_name: str):
    url = f"{DATABASE_URL_TEMPLATE}/{db_name}?charset=utf8mb4"
    return create_engine(
        url,
        poolclass=QueuePool,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


def get_engine(db_name: str):
    if db_name not in _engines:
        _engines[db_name] = _build_engine(db_name)
    return _engines[db_name]


def get_session(db_name: str) -> Session:
    engine = get_engine(db_name)
    if db_name not in _SessionLocals:
        _SessionLocals[db_name] = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return _SessionLocals[db_name]()


def get_template_db() -> Generator[Session, None, None]:
    db = get_session(settings.MYSQL_TEMPLATE_DB)
    try:
        yield db
    finally:
        db.close()


def get_hospital_db(hospital_id: str) -> Generator[Session, None, None]:
    db_name = f"hospital_{hospital_id}"
    db = get_session(db_name)
    try:
        yield db
    finally:
        db.close()


def get_all_hospital_ids() -> List[str]:
    """从 template 库 hospital_tenant 表获取所有活跃医院 ID"""
    db = get_session(settings.MYSQL_TEMPLATE_DB)
    try:
        rows = db.execute(
            text("SELECT hospital_id FROM hospital_tenant WHERE is_active = 1")
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        db.close()
