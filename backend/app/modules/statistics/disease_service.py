"""统计分析-疾病维度统计服务（新增文件）。

设计说明：
1. 全部基于既有三表关联（report_info / report_interpretation / indicator_judgment），
   疾病维度通过 LEFT JOIN disease_mapping 聚合得到，未命中映射的指标归入"其他"；
2. stat_mode=indicator 时完全不依赖 disease_mapping 表（保留指标直统能力）；
3. 检出口径：indicator_judgment.color_level IN ('red','yellow') 的去重报告数；
4. 重大疾病（结论类）关键词匹配为预留能力，本期不实现（见 docs 说明）。
"""
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# 年龄段分段（与既有 cross_compare 保持一致：<30 / 30-45 / 46-60 / >60）
_AGE_GROUP_CASE = (
    "CASE WHEN ri.age < 30 THEN '30岁以下' "
    "WHEN ri.age <= 45 THEN '30-45岁' "
    "WHEN ri.age <= 60 THEN '46-60岁' "
    "ELSE '60岁以上' END"
)

_DIM_EXPR = {
    "gender": "CASE WHEN ri.gender IN ('男', '女') THEN ri.gender ELSE '未知' END",
    "age_group": _AGE_GROUP_CASE,
    "unit": "COALESCE(NULLIF(ri.unit_name, ''), '未知')",
}

# 疾病模式下：指标名 -> 疾病名（未命中归"其他"）
_DISEASE_JOIN = (
    "LEFT JOIN disease_mapping dm "
    "ON dm.item_name_standard = ij.item_name AND dm.enabled = 1"
)


def _dim_expr(dimension: str) -> str:
    return _DIM_EXPR.get(dimension, _DIM_EXPR["unit"])


def _item_expr(stat_mode: str) -> str:
    """统计对象列：indicator=指标名；disease=疾病名（未命中映射归'其他'）"""
    if stat_mode == "disease":
        return "COALESCE(dm.disease_name, '其他')"
    return "ij.item_name"


def indicator_cross(
    db: Session,
    dimension: str,
    stat_mode: str,
    indicators: Optional[List[str]],
    start_date: str,
    end_date: str,
) -> dict:
    """多维交叉对比：dimension × 各指标/疾病 检出率矩阵"""
    dim = _dim_expr(dimension)
    join = _DISEASE_JOIN if stat_mode == "disease" else ""
    item_col = _item_expr(stat_mode)

    # 1) 各维度值样本量（日期区间内有报告的去重人数口径为报告数）
    sample_sql = f"""
        SELECT {dim} AS label, COUNT(DISTINCT ri.id) AS sample_size
        FROM report_info ri
        WHERE ri.report_date BETWEEN :start AND :end
        GROUP BY label
        ORDER BY sample_size DESC
    """
    samples = {
        r.label: r.sample_size
        for r in db.execute(text(sample_sql), {"start": start_date, "end": end_date})
    }

    # 2) 各维度值 × 统计对象 的检出数
    params = {"start": start_date, "end": end_date}
    indicator_filter = ""
    if indicators:
        names = ",".join(f":ind{i}" for i in range(len(indicators)))
        indicator_filter = f"AND ij.item_name IN ({names})"
        for i, name in enumerate(indicators):
            params[f"ind{i}"] = name
    count_sql = f"""
        SELECT {dim} AS label, {item_col} AS item_name, COUNT(DISTINCT ri.id) AS cnt
        FROM indicator_judgment ij
        JOIN report_interpretation interp ON ij.interpretation_id = interp.id
        JOIN report_info ri ON interp.report_id = ri.id
        {join}
        WHERE ri.report_date BETWEEN :start AND :end
          AND ij.color_level IN ('red', 'yellow')
          {indicator_filter}
        GROUP BY label, item_name
    """
    rows = db.execute(text(count_sql), params).fetchall()

    # 3) 组装矩阵
    bucket = {}
    for r in rows:
        bucket.setdefault(r.label, {})[r.item_name] = r.cnt
    data = []
    for label, sample_size in samples.items():
        items_map = bucket.get(label, {})
        items = [
            {
                "name": name,
                "count": cnt,
                "rate": round(cnt / sample_size * 100, 1) if sample_size else 0,
            }
            for name, cnt in sorted(items_map.items(), key=lambda kv: kv[1], reverse=True)
        ]
        data.append({"label": label, "sample_size": sample_size, "items": items})
    return {"dimension": dimension, "stat_mode": stat_mode, "data": data}


def disease_trend(
    db: Session,
    stat_mode: str,
    category: Optional[str],
    diseases: Optional[List[str]],
    years: int,
) -> dict:
    """变化趋势：近 N 年各病种/指标 检出率趋势"""
    # 1) 确定年份序列：取数据中最新的 N 个年份，升序返回
    year_sql = """
        SELECT DISTINCT YEAR(report_date) AS y FROM report_info
        ORDER BY y DESC LIMIT :n
    """
    year_list = sorted(
        [r.y for r in db.execute(text(year_sql), {"n": years}) if r.y is not None]
    )
    if not year_list:
        return {"stat_mode": stat_mode, "category": category, "years": [], "series": []}

    # 2) 每年样本量
    sample_sql = """
        SELECT YEAR(report_date) AS y, COUNT(DISTINCT id) AS sample_size
        FROM report_info GROUP BY y
    """
    samples = {
        r.y: r.sample_size for r in db.execute(text(sample_sql))
    }

    # 3) 确定统计对象清单
    join = _DISEASE_JOIN if stat_mode == "disease" else ""
    item_col = _item_expr(stat_mode)
    params = {"y0": year_list[0], "y1": year_list[-1]}
    filters = ""
    if stat_mode == "disease" and category:
        filters += " AND dm.disease_category = :category"
        params["category"] = category
    if diseases:
        names = ",".join(f":d{i}" for i in range(len(diseases)))
        # disease 模式按疾病名过滤，indicator 模式按指标名过滤
        filters += f" AND {item_col} IN ({names})"
        for i, name in enumerate(diseases):
            params[f"d{i}"] = name

    count_sql = f"""
        SELECT YEAR(ri.report_date) AS y, {item_col} AS item_name, COUNT(DISTINCT ri.id) AS cnt
        FROM indicator_judgment ij
        JOIN report_interpretation interp ON ij.interpretation_id = interp.id
        JOIN report_info ri ON interp.report_id = ri.id
        {join}
        WHERE YEAR(ri.report_date) BETWEEN :y0 AND :y1
          AND ij.color_level IN ('red', 'yellow')
          {filters}
        GROUP BY y, item_name
    """
    rows = db.execute(text(count_sql), params).fetchall()

    # 4) 组装 series（无检出的年份补 0，保证趋势图连续）
    series_map = {}
    for r in rows:
        series_map.setdefault(r.item_name, {})[r.y] = r.cnt
    series = []
    for name, year_cnt in sorted(series_map.items()):
        yearly = []
        for y in year_list:
            cnt = year_cnt.get(y, 0)
            size = samples.get(y, 0)
            yearly.append(
                {
                    "year": y,
                    "count": cnt,
                    "sample_size": size,
                    "rate": round(cnt / size * 100, 1) if size else 0,
                }
            )
        series.append({"name": name, "yearly": yearly})
    return {"stat_mode": stat_mode, "category": category, "years": year_list, "series": series}


def unit_disease_spectrum(
    db: Session,
    unit_name: Optional[str],
    topn: int,
    stat_mode: str,
    start_date: str,
    end_date: str,
) -> dict:
    """单位疾病谱与健康画像：TOP N 疾病/指标 + 疾病类别分布"""
    unit_filter = "AND ri.unit_name = :unit_name" if unit_name else ""
    params = {"start": start_date, "end": end_date, "topn": topn}
    if unit_name:
        params["unit_name"] = unit_name

    total_sql = f"""
        SELECT COUNT(DISTINCT ri.id) AS total FROM report_info ri
        WHERE ri.report_date BETWEEN :start AND :end {unit_filter}
    """
    total = db.execute(text(total_sql), params).fetchone().total or 0

    join = _DISEASE_JOIN if stat_mode == "disease" else ""
    item_col = _item_expr(stat_mode)
    top_sql = f"""
        SELECT {item_col} AS item_name, COUNT(DISTINCT ri.id) AS cnt
        FROM indicator_judgment ij
        JOIN report_interpretation interp ON ij.interpretation_id = interp.id
        JOIN report_info ri ON interp.report_id = ri.id
        {join}
        WHERE ri.report_date BETWEEN :start AND :end
          AND ij.color_level IN ('red', 'yellow')
          {unit_filter}
        GROUP BY item_name
        ORDER BY cnt DESC
        LIMIT :topn
    """
    top_rows = db.execute(text(top_sql), params).fetchall()
    top_diseases = [
        {
            "rank": i + 1,
            "name": r.item_name,
            "count": r.cnt,
            "rate": round(r.cnt / total * 100, 1) if total else 0,
        }
        for i, r in enumerate(top_rows)
    ]

    # 疾病类别分布：disease 模式取 disease_mapping.disease_class；
    # indicator 模式取 report_indicator.category（经 indicator_judgment.indicator_id 关联）
    if stat_mode == "disease":
        class_col = "COALESCE(dm.disease_class, '未分类')"
        class_join = _DISEASE_JOIN
    else:
        class_col = "COALESCE(NULLIF(rind.category, ''), '未分类')"
        class_join = "LEFT JOIN report_indicator rind ON ij.indicator_id = rind.id"
    class_sql = f"""
        SELECT {class_col} AS class_name, COUNT(DISTINCT ri.id) AS cnt
        FROM indicator_judgment ij
        JOIN report_interpretation interp ON ij.interpretation_id = interp.id
        JOIN report_info ri ON interp.report_id = ri.id
        {class_join}
        WHERE ri.report_date BETWEEN :start AND :end
          AND ij.color_level IN ('red', 'yellow')
          {unit_filter}
        GROUP BY class_name
        ORDER BY cnt DESC
    """
    class_rows = db.execute(text(class_sql), params).fetchall()
    class_distribution = [{"name": r.class_name, "value": r.cnt} for r in class_rows]

    return {
        "unit_name": unit_name,
        "stat_mode": stat_mode,
        "total": total,
        "top_diseases": top_diseases,
        "class_distribution": class_distribution,
    }
