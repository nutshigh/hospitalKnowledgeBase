"""统计分析-疾病维度相关的请求/响应模型与枚举（新增文件，不影响既有 statistics 模块）。"""
from typing import List, Literal, Optional

from pydantic import BaseModel

# 统计口径：indicator=直接按指标统计（不依赖映射表）；disease=经 disease_mapping 聚合为疾病
StatMode = Literal["indicator", "disease"]

# 交叉对比维度（region 地域维度由上层调用方（Java 转发层）按 unit 聚合实现，本层不涉及）
CrossDimension = Literal["gender", "age_group", "unit"]

# 疾病分类（对应 disease_mapping.disease_category）
DiseaseCategory = Literal["CHRONIC", "MAJOR", "OTHER"]


class IndicatorCrossResult(BaseModel):
    dimension: str
    stat_mode: str
    data: List[dict]  # [{label, sample_size, items: [{name, count, rate}]}]


class DiseaseTrendResult(BaseModel):
    stat_mode: str
    category: Optional[str]
    years: List[int]
    series: List[dict]  # [{name, yearly: [{year, count, sample_size, rate}]}]


class UnitDiseaseSpectrumResult(BaseModel):
    unit_name: Optional[str]
    stat_mode: str
    total: int
    top_diseases: List[dict]  # [{rank, name, count, rate}]
    class_distribution: List[dict]  # [{name, value}]
