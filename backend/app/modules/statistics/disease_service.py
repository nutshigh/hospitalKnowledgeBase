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

# 排除演示数据的 WHERE 片段（所有统计查询统一过滤）
_DEMO_FILTER = "AND (ri.check_type IS NULL OR ri.check_type != 'DEMO')"

# 按指标模式只统计指标来源异常（排除总检建议来源）
_SRC_INDICATOR_FILTER = "AND ij.source = 'indicator'"

# 按疾病模式：排除已有 indicator 源的指标（"只在按指标中显示"，不留 conclusion 副本亦不留 indicator 副本）
_EXCLUDE_INDICATOR_ITEMS_FILTER = (
    "AND NOT EXISTS ("
    "SELECT 1 FROM indicator_judgment ij2"
    " WHERE ij2.interpretation_id = ij.interpretation_id"
    " AND ij2.item_name = ij.item_name AND ij2.source = 'indicator')"
)
# 指标标准名兜底 join：统计一律以 report_indicator.item_name_standard 为准，
# 该列缺失时回退 indicator_judgment.item_name（结论线条目无标准名时的兜底）
_INDICATOR_JOIN = "LEFT JOIN report_indicator rind ON ij.indicator_id = rind.id"

_DISEASE_JOIN = (
    "LEFT JOIN disease_mapping dm "
    "ON dm.item_name_standard COLLATE utf8mb4_unicode_ci = COALESCE(rind.item_name_standard, ij.item_name)"
    " AND dm.enabled = 1"
)


# 按疾病模式: disease_hit 表(解读完成后固化命中的病种, 见 app/modules/risk)
# 维度: gender/age_group 取 report_info, unit 取 disease_hit.unit_name(报告冗余)
_HIT_DIM_EXPR = {
    "gender": "CASE WHEN ri.gender IN ('男', '女') THEN ri.gender ELSE '未知' END",
    "age_group": _AGE_GROUP_CASE,
    "unit": "COALESCE(NULLIF(dh.unit_name, ''), '未知')",
}


def _dim_expr_hit(dimension: str) -> str:
    return _HIT_DIM_EXPR.get(dimension, _HIT_DIM_EXPR["unit"])


def _dim_expr(dimension: str) -> str:
    return _DIM_EXPR.get(dimension, _DIM_EXPR["unit"])


def _item_expr(stat_mode: str) -> str:
    """统计对象列：indicator=标准指标名（rind.item_name_standard，缺失回退原文名）；disease=疾病名（未命中映射归'其他'）"""
    if stat_mode == "disease":
        return "COALESCE(dm.disease_name, '其他')"
    return "COALESCE(rind.item_name_standard, ij.item_name)"


def indicator_cross(
    db: Session,
    dimension: str,
    stat_mode: str,
    indicators: Optional[List[str]],
    start_date: str,
    end_date: str,
) -> dict:
    """多维交叉对比：dimension × 各指标/疾病 检出率矩阵"""
    if stat_mode == "disease":
        return _indicator_cross_disease(db, dimension, start_date, end_date)
    dim = _dim_expr(dimension)
    join = _INDICATOR_JOIN + (_DISEASE_JOIN if stat_mode == "disease" else "")
    item_col = _item_expr(stat_mode)

    # 1) 各维度值样本量（日期区间内有报告的去重人数口径为报告数）
    sample_sql = f"""
        SELECT {dim} AS label, COUNT(DISTINCT ri.user_id, ri.report_date) AS sample_size
        FROM report_info ri
        WHERE ri.report_date BETWEEN :start AND :end
        {_DEMO_FILTER}
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
        indicator_filter = f"AND COALESCE(rind.item_name_standard, ij.item_name) IN ({names})"
        for i, name in enumerate(indicators):
            params[f"ind{i}"] = name
    # 按指标模式只统计指标类异常（排除总检建议类）
    src_filter = _SRC_INDICATOR_FILTER if stat_mode == "indicator" else ""
    # 按疾病模式去重（同报告同指标已有 indicator 则排除 conclusion）
    dedup_filter = _EXCLUDE_INDICATOR_ITEMS_FILTER if stat_mode == "disease" else ""
    count_sql = f"""
        SELECT {dim} AS label, {item_col} AS item_name, COUNT(DISTINCT ri.user_id, ri.report_date) AS cnt
        FROM indicator_judgment ij
        JOIN report_interpretation interp ON ij.interpretation_id = interp.id
        JOIN report_info ri ON interp.report_id = ri.id
        {join}
        WHERE ri.report_date BETWEEN :start AND :end
          {_DEMO_FILTER}
          {src_filter}
          {dedup_filter}
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
    """变化趋势：近 N 年各病种/指标 检出率趋势（年份序列连续，缺数据年份补零）"""
    if stat_mode == "disease":
        return _disease_trend_disease(db, category, diseases, years)
    # 1) 确定年份序列：以数据中最大年份为锚点，向前生成连续的 N 年序列
    max_year_sql = """
        SELECT MAX(YEAR(report_date)) AS y FROM report_info
        WHERE check_type IS NULL OR check_type != 'DEMO'
    """
    max_year = db.execute(text(max_year_sql)).scalar()
    if not max_year:
        return {"stat_mode": stat_mode, "category": category, "years": [], "series": []}
    year_list = list(range(max_year - years + 1, max_year + 1))

    # 2) 每年样本量
    sample_sql = """
        SELECT YEAR(report_date) AS y, COUNT(DISTINCT user_id, report_date) AS sample_size
        FROM report_info
        WHERE check_type IS NULL OR check_type != 'DEMO'
        GROUP BY y
    """
    samples = {
        r.y: r.sample_size for r in db.execute(text(sample_sql))
    }

    # 3) 确定统计对象清单
    join = _INDICATOR_JOIN + (_DISEASE_JOIN if stat_mode == "disease" else "")
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

    # 按指标模式只统计指标类异常
    src_filter = _SRC_INDICATOR_FILTER if stat_mode == "indicator" else ""
    dedup_filter = _EXCLUDE_INDICATOR_ITEMS_FILTER if stat_mode == "disease" else ""
    count_sql = f"""
        SELECT YEAR(ri.report_date) AS y, {item_col} AS item_name, COUNT(DISTINCT ri.user_id, ri.report_date) AS cnt
        FROM indicator_judgment ij
        JOIN report_interpretation interp ON ij.interpretation_id = interp.id
        JOIN report_info ri ON interp.report_id = ri.id
        {join}
        WHERE YEAR(ri.report_date) BETWEEN :y0 AND :y1
          {_DEMO_FILTER}
          {src_filter}
          {dedup_filter}
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
    if stat_mode == "disease":
        return _unit_spectrum_disease(db, unit_name, topn, start_date, end_date)
    unit_filter = "AND ri.unit_name = :unit_name" if unit_name else ""
    params = {"start": start_date, "end": end_date, "topn": topn}
    if unit_name:
        params["unit_name"] = unit_name

    total_sql = f"""
        SELECT COUNT(DISTINCT ri.user_id, ri.report_date) AS total FROM report_info ri
        WHERE ri.report_date BETWEEN :start AND :end {_DEMO_FILTER} {unit_filter}
    """
    total = db.execute(text(total_sql), params).fetchone().total or 0

    join = _INDICATOR_JOIN + (_DISEASE_JOIN if stat_mode == "disease" else "")
    item_col = _item_expr(stat_mode)
    src_filter = _SRC_INDICATOR_FILTER if stat_mode == "indicator" else ""
    dedup_filter = _EXCLUDE_INDICATOR_ITEMS_FILTER if stat_mode == "disease" else ""
    top_sql = f"""
        SELECT {item_col} AS item_name, COUNT(DISTINCT ri.user_id, ri.report_date) AS cnt
        FROM indicator_judgment ij
        JOIN report_interpretation interp ON ij.interpretation_id = interp.id
        JOIN report_info ri ON interp.report_id = ri.id
        {join}
        WHERE ri.report_date BETWEEN :start AND :end
          {_DEMO_FILTER}
          {src_filter}
          {dedup_filter}
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
    class_col = (
        "COALESCE(dm.disease_class, '未分类')"
        if stat_mode == "disease"
        else "COALESCE(NULLIF(rind.category, ''), '未分类')"
    )
    class_join = _INDICATOR_JOIN + (_DISEASE_JOIN if stat_mode == "disease" else "")
    class_sql = f"""
        SELECT {class_col} AS class_name, COUNT(DISTINCT ri.user_id, ri.report_date) AS cnt
        FROM indicator_judgment ij
        JOIN report_interpretation interp ON ij.interpretation_id = interp.id
        JOIN report_info ri ON interp.report_id = ri.id
        {class_join}
        WHERE ri.report_date BETWEEN :start AND :end
          {_DEMO_FILTER}
          {src_filter}
          {dedup_filter}
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


# ---------------------------------------------------------------------------
# disease 模式: 直查 disease_hit(风险引擎固化结果, 见 app/modules/risk)
# 样本量口径不变(仍按 report_info 日期区间), 统计对象为 dh.disease_name
# ---------------------------------------------------------------------------

# 统计口径(2026-08-19 决策): 非疾病条目仅作"判断慢病/重疾的线索指标",
# 不进入统计数字。映射表保留(供解读链接), 统计层统一剔除。
# 2026-08-20 收窄: 仅剔除"纯指标自映射杂项"(指标名==病种名, 如 肌酸激酶/尿酸),
# 屈光不正/扁桃体肥大/龋齿 等真实体检异常发现保留, 按 disease_category 归入"异常"。
_STATS_EXCLUDED_DISEASES = (
    "肌酸激酶", "尿酸",
)


def _excluded_sql() -> str:
    return " AND dh.disease_name NOT IN (" + ",".join(
        f"'{n}'" for n in _STATS_EXCLUDED_DISEASES
    ) + ")"


def _excluded_item_sql() -> str:
    """按判定名(ij.item_name)过滤的非疾病条目 SQL(health-profile 等旧接口用)。"""
    return " AND ij.item_name NOT IN (" + ",".join(
        f"'{n}'" for n in _STATS_EXCLUDED_DISEASES
    ) + ")"

def _indicator_cross_disease(db: Session, dimension: str, start_date: str, end_date: str) -> dict:
    dim_hit = _dim_expr_hit(dimension)
    sample_sql = f"""
        SELECT {_dim_expr(dimension)} AS label, COUNT(DISTINCT ri.user_id, ri.report_date) AS sample_size
        FROM report_info ri
        WHERE ri.report_date BETWEEN :start AND :end
        {_DEMO_FILTER}
        GROUP BY label
        ORDER BY sample_size DESC
    """
    samples = {
        r.label: r.sample_size
        for r in db.execute(text(sample_sql), {"start": start_date, "end": end_date})
    }
    count_sql = f"""
        SELECT {dim_hit} AS label, dh.disease_name AS item_name,
               COALESCE(MAX(dh.disease_category), 'OTHER') AS category,
               COUNT(DISTINCT dh.user_id, dh.report_date) AS cnt
        FROM disease_hit dh
        JOIN report_info ri ON ri.id = dh.report_id
        WHERE dh.report_date BETWEEN :start AND :end
          {_DEMO_FILTER}
          {_excluded_sql()}
        GROUP BY label, item_name
    """
    rows = db.execute(text(count_sql), {"start": start_date, "end": end_date}).fetchall()
    bucket = {}
    cat_of = {}
    for r in rows:
        bucket.setdefault(r.label, {})[r.item_name] = r.cnt
        cat_of.setdefault(r.item_name, r.category)
    data = []
    for label, sample_size in samples.items():
        items_map = bucket.get(label, {})
        items = [
            {
                "name": name,
                "category": cat_of.get(name, "OTHER"),
                "count": cnt,
                "rate": round(cnt / sample_size * 100, 1) if sample_size else 0,
            }
            for name, cnt in sorted(items_map.items(), key=lambda kv: kv[1], reverse=True)
        ]
        data.append({"label": label, "sample_size": sample_size, "items": items})
    return {"dimension": dimension, "stat_mode": "disease", "data": data}


def _disease_trend_disease(db: Session, category, diseases, years: int) -> dict:
    max_year_sql = """
        SELECT MAX(YEAR(report_date)) AS y FROM report_info
        WHERE check_type IS NULL OR check_type != 'DEMO'
    """
    max_year = db.execute(text(max_year_sql)).scalar()
    if not max_year:
        return {"stat_mode": "disease", "category": category, "years": [], "series": []}
    year_list = list(range(max_year - years + 1, max_year + 1))
    sample_sql = """
        SELECT YEAR(report_date) AS y, COUNT(DISTINCT user_id, report_date) AS sample_size
        FROM report_info
        WHERE check_type IS NULL OR check_type != 'DEMO'
        GROUP BY y
    """
    samples = {r.y: r.sample_size for r in db.execute(text(sample_sql))}

    params = {"y0": year_list[0], "y1": year_list[-1]}
    filters = ""
    if category:
        filters += " AND dh.disease_category = :category"
        params["category"] = category
    if diseases:
        names = ",".join(f":d{i}" for i in range(len(diseases)))
        filters += " AND dh.disease_name IN ({names})"
        for i, name in enumerate(diseases):
            params[f"d{i}"] = name
    count_sql = f"""
        SELECT YEAR(dh.report_date) AS y, dh.disease_name AS item_name,
               COUNT(DISTINCT dh.user_id, dh.report_date) AS cnt
        FROM disease_hit dh
        JOIN report_info ri ON ri.id = dh.report_id
        WHERE YEAR(dh.report_date) BETWEEN :y0 AND :y1
          {_DEMO_FILTER}
          {_excluded_sql()}
          {filters}
        GROUP BY y, item_name
    """
    rows = db.execute(text(count_sql), params).fetchall()
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
    return {"stat_mode": "disease", "category": category, "years": year_list, "series": series}


def _unit_spectrum_disease(db: Session, unit_name, topn: int, start_date: str, end_date: str) -> dict:
    unit_filter = "AND dh.unit_name = :unit_name" if unit_name else ""
    params = {"start": start_date, "end": end_date, "topn": topn}
    if unit_name:
        params["unit_name"] = unit_name
    total_sql = f"""
        SELECT COUNT(DISTINCT ri.user_id, ri.report_date) AS total FROM report_info ri
        WHERE ri.report_date BETWEEN :start AND :end {_DEMO_FILTER}
          {"AND ri.unit_name = :unit_name" if unit_name else ""}
    """
    total = db.execute(text(total_sql), params).fetchone().total or 0
    top_sql = f"""
        SELECT dh.disease_name AS item_name,
               COUNT(DISTINCT dh.user_id, dh.report_date) AS cnt
        FROM disease_hit dh
        JOIN report_info ri ON ri.id = dh.report_id
        WHERE dh.report_date BETWEEN :start AND :end
          {_DEMO_FILTER}
          {_excluded_sql()}
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
    class_sql = f"""
        SELECT COALESCE(NULLIF(dh.disease_class, ''), '未分类') AS class_name,
               COUNT(DISTINCT dh.user_id, dh.report_date) AS cnt
        FROM disease_hit dh
        JOIN report_info ri ON ri.id = dh.report_id
        WHERE dh.report_date BETWEEN :start AND :end
          {_DEMO_FILTER}
          {_excluded_sql()}
          {unit_filter}
        GROUP BY class_name
        ORDER BY cnt DESC
    """
    class_rows = db.execute(text(class_sql), params).fetchall()
    class_distribution = [{"name": r.class_name, "value": r.cnt} for r in class_rows]
    return {
        "unit_name": unit_name,
        "stat_mode": "disease",
        "total": total,
        "top_diseases": top_diseases,
        "class_distribution": class_distribution,
    }
