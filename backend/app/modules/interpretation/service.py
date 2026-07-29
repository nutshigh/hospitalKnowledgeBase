from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session

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
    # 字符重叠率 ≥ 80% 且长度差 ≤ 2
    sa, sb = set(na), set(nb)
    overlap = len(sa & sb) / min(len(sa), len(sb))
    if overlap >= 0.8 and abs(len(na) - len(nb)) <= 2:
        return True
    return False


def get_judgments_with_indicator_detail(db: Session, interpretation_id: int) -> list[dict]:
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT j.indicator_id, j.item_name, j.result_value, j.deviation, j.color_level, "
        "i.unit, i.ref_range_low, i.ref_range_high, i.raw_text "
        "FROM indicator_judgment j "
        "LEFT JOIN report_indicator i ON i.id = j.indicator_id "
        "WHERE j.interpretation_id = :iid ORDER BY j.id"
    ), {"iid": interpretation_id}).fetchall()

    all_items = [
        {"indicator_id": r[0], "item_name": r[1], "result_value": r[2],
         "deviation": r[3], "color_level": r[4], "unit": r[5],
         "ref_range_low": r[6], "ref_range_high": r[7], "raw_text": r[8]}
        for r in rows
    ]

    regular_items = [it for it in all_items if not it["raw_text"]]
    conclusion_items = [it for it in all_items if it["raw_text"]]

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

    return regular_items + kept


def _link_disease_mapping(db, name_a: str, name_b: str) -> None:
    """确保 name_a 和 name_b 指向同一条 disease_mapping 记录。"""
    from sqlalchemy import text
    row_a = db.execute(text(
        "SELECT id, item_name_standard FROM disease_mapping WHERE disease_name = :dn OR item_name_standard = :nm LIMIT 1"
    ), {"dn": name_a, "nm": name_a}).fetchone()
    row_b = db.execute(text(
        "SELECT id, item_name_standard FROM disease_mapping WHERE disease_name = :dn OR item_name_standard = :nm LIMIT 1"
    ), {"dn": name_b, "nm": name_b}).fetchone()

    std_a = row_a[1] if row_a else name_a
    std_b = row_b[1] if row_b else name_b
    if std_a == std_b:
        return

    target = std_a if len(std_a) <= len(std_b) else std_b
    if row_b:
        db.execute(text(
            "UPDATE disease_mapping SET item_name_standard = :target WHERE id = :id"
        ), {"target": target, "id": row_b[0]})
    elif not row_a:
        db.execute(text(
            "INSERT INTO disease_mapping (item_name_standard, item_name, disease_name, disease_category, sort_code) "
            "VALUES (:std, :orig, :dn, 'OTHER', 200)"
            "ON DUPLICATE KEY UPDATE disease_name=VALUES(disease_name)"
        ), {"std": target, "orig": name_b, "dn": name_b})
    db.commit()


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
