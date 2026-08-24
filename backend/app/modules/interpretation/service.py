from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment, TriageRule
from app.modules.interpretation.rules_engine import rules_engine
from app.modules.interpretation.schemas import TriageRuleCreate, TriageRuleUpdate
from app.modules.report.models import ReportInfo, ReportIndicator
from app.core.rabbitmq import rabbitmq, TaskMessage


# ---- Triage Rules CRUD ----

def list_rules(db: Session) -> List[TriageRule]:
    return db.query(TriageRule).order_by(TriageRule.priority).all()


def get_rule(db: Session, rule_id: int) -> Optional[TriageRule]:
    return db.query(TriageRule).filter(TriageRule.id == rule_id).first()


def create_rule(db: Session, data: TriageRuleCreate) -> TriageRule:
    rule = TriageRule(
        rule_name=data.rule_name, rule_type=data.rule_type,
        indicator_code=data.indicator_code, conditions=data.conditions,
        color_level=data.color_level, priority=data.priority,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def update_rule(db: Session, rule_id: int, data: TriageRuleUpdate) -> Optional[TriageRule]:
    rule = get_rule(db, rule_id)
    if not rule:
        return None
    for field in ("rule_name", "rule_type", "indicator_code", "conditions", "color_level", "priority", "is_active"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(rule, field, val)
    db.commit()
    db.refresh(rule)
    return rule


def delete_rule(db: Session, rule_id: int) -> bool:
    rule = get_rule(db, rule_id)
    if not rule:
        return False
    db.delete(rule)
    db.commit()
    return True


# ---- Interpretation Pipeline ----

def process_interpretation(db: Session, report_id: int, hospital_id: str):
    """触发 interpretation Agent 处理（薄包装，实际逻辑在 ai/agents/interp_graph.py）"""
    from app.ai.agents import run_interpretation_agent
    run_interpretation_agent(hospital_id, db, report_id)


def refresh_interpretation_counts(db: Session, interpretation_id: int) -> None:
    """用去重后的实际数据刷新 report_interpretation 的红/黄/绿区统计。"""
    items = get_judgments_with_indicator_detail(db, interpretation_id)
    red_count = sum(1 for it in items if it.get("color_level") == "red")
    yellow_count = sum(1 for it in items if it.get("color_level") == "yellow")
    green_count = sum(1 for it in items if it.get("color_level") == "green")
    if red_count > 0:
        overall = "red"
    elif yellow_count > 0:
        overall = "yellow"
    else:
        overall = "green"
    db.execute(
        text("UPDATE report_interpretation SET red_count=:r, yellow_count=:y, green_count=:g, overall_level=:o WHERE id=:iid"),
        {"r": red_count, "y": yellow_count, "g": green_count, "o": overall, "iid": interpretation_id},
    )
    db.commit()


def get_interpretation(db: Session, report_id: int) -> Optional[ReportInterpretation]:
    return db.query(ReportInterpretation).filter(ReportInterpretation.report_id == report_id).first()


def get_judgments(db: Session, interpretation_id: int) -> List[IndicatorJudgment]:
    return db.query(IndicatorJudgment).filter(IndicatorJudgment.interpretation_id == interpretation_id).all()


def _fuzzy_overlap(a: str, b: str) -> bool:
    """判断两个指标名是否指向同一异常（模糊匹配，仅当两者足够相似且非单字匹配）。"""
    import re
    def _norm(s: str) -> str:
        s = re.sub(r'[（）()>]', '', s)
        s = re.sub(r'^(血清|血|全血|血浆)', '', s)
        s = re.sub(r'(检查|测定|检测|试验)$', '', s)
        s = re.sub(r'[\d.]+$', '', s)
        s = s.replace(' ', '').strip()
        return s
    na = _norm(a)
    nb = _norm(b)
    if not na or not nb or len(na) < 2 or len(nb) < 2:
        return False
    if na == nb:
        return True
    # 子串包含：较短者 >= 3 字符且在较长者内部
    shorter = na if len(na) <= len(nb) else nb
    longer = nb if shorter == na else na
    if len(shorter) >= 3 and shorter in longer:
        return True
    # 字符重叠率 ≥ 80% 且长度差 ≤ 2，且双方均 ≥ 4 字符（防短词误匹配如"甲胎蛋白"vs"白蛋白"）
    sa, sb = set(na), set(nb)
    overlap = len(sa & sb) / min(len(sa), len(sb))
    if overlap >= 0.8 and abs(len(na) - len(nb)) <= 2 and len(na) >= 4 and len(nb) >= 4:
        return True
    return False


def get_judgments_with_indicator_detail(db: Session, interpretation_id: int) -> list[dict]:
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT j.indicator_id, j.item_name, j.result_value, j.deviation, j.color_level, "
        "i.unit, i.ref_range_low, i.ref_range_high, i.raw_text, j.source, j.explanation "
        "FROM indicator_judgment j "
        "LEFT JOIN report_indicator i ON i.id = j.indicator_id "
        "WHERE j.interpretation_id = :iid ORDER BY j.id"
    ), {"iid": interpretation_id}).fetchall()

    all_items = [
        {"indicator_id": r[0], "item_name": r[1], "result_value": r[2],
         "deviation": r[3], "color_level": r[4], "unit": r[5],
         "ref_range_low": r[6], "ref_range_high": r[7], "raw_text": r[8],
         "source": r[9], "explanation": r[10]}
        for r in rows
    ]

    regular_items = [it for it in all_items if it.get("source") != "conclusion"]
    conclusion_items = [it for it in all_items if it.get("source") == "conclusion"]

    if not conclusion_items:
        return all_items

    # 去重：结论型异常如果与化验型异常（同报告内）指向同一疾病，剔除
    reg_anomaly_names = {it["item_name"] for it in regular_items
                         if it.get("color_level") in ("yellow", "red")}

    kept = []
    for citem in conclusion_items:
        # 精确同名
        if citem["item_name"] in reg_anomaly_names:
            continue

        # 模糊匹配
        fuzzy_matched = False
        for rname in reg_anomaly_names:
            if _fuzzy_overlap(citem["item_name"], rname):
                _link_disease_mapping(db, citem["item_name"], rname)
                fuzzy_matched = True
                break
        if fuzzy_matched:
            continue

        # disease_mapping 兜底：查映射表是否指向同一疾病
        dm_matched = False
        for rname in reg_anomaly_names:
            dm_r = db.execute(text(
                "SELECT disease_name FROM disease_mapping WHERE item_name_standard = :nm OR disease_name = :nm LIMIT 1"
            ), {"nm": rname}).scalar()
            dm_c = db.execute(text(
                "SELECT disease_name FROM disease_mapping WHERE item_name_standard = :nm OR disease_name = :nm LIMIT 1"
            ), {"nm": citem["item_name"]}).scalar()
            if dm_r and dm_c and dm_r == dm_c:
                dm_matched = True
                break
        if dm_matched:
            continue

        kept.append(citem)

    # Rule 2: 结论异常 vs 绿色指标同名 → 以结论为准，剔除绿色指标项
    kept_names = {it["item_name"] for it in kept}
    regular_items = [it for it in regular_items
                     if not (it["item_name"] in kept_names and it.get("color_level") == "green")]

    return regular_items + kept


def _link_disease_mapping(db, conclusion_name: str, regular_name: str) -> None:
    """统一 disease_mapping：以指标型名称（regular_name）的 disease_name 为准，
    将结论型名称（conclusion_name）的条目删除，避免重复。"""
    from sqlalchemy import text
    row_c = db.execute(text(
        "SELECT id, disease_name FROM disease_mapping WHERE disease_name = :dn OR item_name_standard = :nm LIMIT 1"
    ), {"dn": conclusion_name, "nm": conclusion_name}).fetchone()
    row_r = db.execute(text(
        "SELECT id, disease_name FROM disease_mapping WHERE disease_name = :dn OR item_name_standard = :nm LIMIT 1"
    ), {"dn": regular_name, "nm": regular_name}).fetchone()

    if not row_c or not row_r:
        return
    if row_c[1] == row_r[1]:
        return
    if row_c[0] == row_r[0]:
        return

    # 删除结论型条目（指标型条目已覆盖同名 disease）
    try:
        db.execute(text("DELETE FROM disease_mapping WHERE id = :id"), {"id": row_c[0]})
        db.commit()
    except Exception:
        pass


def get_high_risk_list(db: Session, hospital_id: str) -> List[dict]:
    rows = (
        db.query(ReportInterpretation, ReportInfo)
        .join(ReportInfo, ReportInterpretation.report_id == ReportInfo.id)
        .filter(ReportInterpretation.overall_level == "red")
        .order_by(ReportInterpretation.red_count.desc())
        .all()
    )
    return [
        {"interpretation_id": i.id, "report_id": i.report_id, "user_id": r.user_id,
         "name": r.name, "unit_name": r.unit_name, "red_count": i.red_count,
         "created_at": i.created_at}
        for i, r in rows
    ]
