"""统计分析-疾病维度端点（新增文件）。

三个端点均面向服务端转发调用（sz-mana Java 后端），
鉴权使用服务间共享密钥（require_service_client），不使用终端用户 JWT。
既有 statistics 端点不受影响。
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.service_auth import require_service_client
from app.modules.statistics import disease_schemas, disease_service

router = APIRouter(dependencies=[Depends(require_service_client)])


def _get_db(hospital_id: str = Depends(require_service_client)):
    gen = get_hospital_db(hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@router.get("/indicator-cross", response_model=disease_schemas.IndicatorCrossResult)
def indicator_cross(
    start_date: date = Query(...),
    end_date: date = Query(...),
    dimension: disease_schemas.CrossDimension = Query("gender"),
    stat_mode: disease_schemas.StatMode = Query("indicator"),
    indicators: Optional[str] = Query(None, description="指标名列表，逗号分隔（仅 indicator 模式生效）"),
    db: Session = Depends(_get_db),
):
    indicator_list = [s.strip() for s in indicators.split(",")] if indicators else None
    return disease_service.indicator_cross(
        db, dimension, stat_mode, indicator_list, str(start_date), str(end_date)
    )


@router.get("/disease-trend", response_model=disease_schemas.DiseaseTrendResult)
def disease_trend(
    years: int = Query(5, ge=1, le=10),
    stat_mode: disease_schemas.StatMode = Query("disease"),
    category: Optional[disease_schemas.DiseaseCategory] = Query(None, description="疾病分类（disease 模式生效）"),
    diseases: Optional[str] = Query(None, description="疾病/指标名列表，逗号分隔，为空则统计该分类全部"),
    unit_names: Optional[str] = Query(None, description="单位名称列表，逗号分隔，为空则统计全部单位"),
    db: Session = Depends(_get_db),
):
    disease_list = [s.strip() for s in diseases.split(",")] if diseases else None
    unit_list = [s.strip() for s in unit_names.split(",") if s.strip()] if unit_names else None
    return disease_service.disease_trend(db, stat_mode, category, disease_list, years, unit_list)


@router.get("/unit-disease-spectrum", response_model=disease_schemas.UnitDiseaseSpectrumResult)
def unit_disease_spectrum(
    start_date: date = Query(...),
    end_date: date = Query(...),
    unit_name: Optional[str] = Query(None, description="单位名称，为空则统计全部单位"),
    unit_names: Optional[str] = Query(None, description="单位名称列表，逗号分隔（多单位过滤，优先于 unit_name）"),
    topn: int = Query(10, ge=1, le=100),
    stat_mode: disease_schemas.StatMode = Query("disease"),
    db: Session = Depends(_get_db),
):
    names = [s.strip() for s in unit_names.split(",") if s.strip()] if unit_names else None
    if names is None and unit_name:
        names = [unit_name]
    return disease_service.unit_disease_spectrum(
        db, names, topn, stat_mode, str(start_date), str(end_date)
    )
