import re
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
    """2026-09-16: 同一报告可能存在多条解读(重跑/重试残留) —— 优先返回**最新已完成**
    行, 无已完成时回退最新行。原实现 first() 无排序, 多条时会随机返回旧行(前端会
    看到旧分级: 欧阳庆 MPV 旧解读为绿)。"""
    q = (db.query(ReportInterpretation)
         .filter(ReportInterpretation.report_id == report_id))
    return (q.filter(ReportInterpretation.status == "completed")
            .order_by(ReportInterpretation.id.desc()).first()
            or q.order_by(ReportInterpretation.id.desc()).first())


def get_judgments(db: Session, interpretation_id: int) -> List[IndicatorJudgment]:
    return db.query(IndicatorJudgment).filter(IndicatorJudgment.interpretation_id == interpretation_id).all()


def _share_prefix5(a: str, b: str) -> bool:
    """2026-09-05: 同一实体变体 —— 剥 括号注释/结论/测定 后前 5 字相同
    ("乙肝两对半(第1,2,4,5项阳性)" vs "乙肝两对半结论")。"""
    import re
    def norm(s: str) -> str:
        s = re.sub(r"[（(].*?[)）]", "", s)
        s = re.sub(r"(结论|测定|检测)", "", s)
        s = s.replace(" ", "").replace("　", "")
        return s
    na, nb = norm(a), norm(b)
    return len(na) >= 5 and len(nb) >= 5 and na[:5] == nb[:5]


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
    """join indicator_judgment 与 report_indicator，返回前端展示所需字段

    含 deviation/color_level（来自 judgment）+ unit/ref_range_low/ref_range_high/category（来自 indicator）。
    """
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT j.indicator_id, j.item_name, j.result_value, j.deviation, j.color_level, "
        "i.unit, i.ref_range_low, i.ref_range_high, i.category, i.raw_text, j.source, j.explanation "
        "FROM indicator_judgment j "
        "LEFT JOIN report_indicator i ON i.id = j.indicator_id "
        "WHERE j.interpretation_id = :iid ORDER BY j.id"
    ), {"iid": interpretation_id}).fetchall()

    all_items = [
        {"indicator_id": r[0], "item_name": r[1], "result_value": r[2],
         "deviation": r[3], "color_level": r[4], "unit": r[5],
         "ref_range_low": r[6], "ref_range_high": r[7], "category": r[8],
         "raw_text": r[9], "source": r[10], "explanation": r[11]}
        for r in rows
    ]

    # 2026-09-01: 绿区展示层垃圾过滤(黄/红区指标与总检异常不动)
    # 2026-09-02: "弃检/未检/放弃"类名称任何区都滤(非指标, 步新宇眼压弃检行)
    from app.modules.report.service import _clean_green_indicator, _ABANDON_ITEM_RE
    cleaned_items = []
    for it in all_items:
        if it.get("source") != "conclusion" and _ABANDON_ITEM_RE.search(it["item_name"]):
            continue
        if it.get("color_level") == "green" and it.get("source") != "conclusion":
            clean = _clean_green_indicator(it["item_name"], it.get("result_value") or "")
            if clean is None:
                continue
            if clean != it["item_name"]:
                it["item_name"] = clean
        cleaned_items.append(it)
    all_items = cleaned_items

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

        # 2026-09-05: 共享前缀变体("乙肝两对半(第…阳性)" vs 指标"乙肝两对半结论")→ 剔除结论侧
        if any(_share_prefix5(citem["item_name"], rn) for rn in reg_anomaly_names):
            continue

        # 模糊匹配
        fuzzy_matched = False
        for rname in reg_anomaly_names:
            if _fuzzy_overlap(citem["item_name"], rname):
                # 2026-08-31: 结论名是疾病全称且显著更长时不剔除 ——
                # "高尿酸血症"(结论, 疾病诊断) vs "尿酸(UA)"(指标简称) 是
                # 不同语义层级, 用户要求两者都展示(指标黄区 + 总检建议)。
                if rname in citem["item_name"] \
                        and len(citem["item_name"]) >= len(rname) + 2 \
                        and re.search(r"(血症|病|症|炎|瘤|癌|肿|硬化|息肉|结节|结石|囊肿|异常)$", citem["item_name"]):
                    continue
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
                # 2026-08-31: 疾病全称豁免 —— 指标"尿酸(UA)" 与结论"高尿酸血症"
                # 映射到同一疾病名, 但结论是总检建议的疾病诊断(用户要求展示),
                # 指标是化验异常(黄区展示), 两者语义层级不同, 不剔除。
                rstem = re.sub(r"[（(].*?[)）]", "", rname).strip()
                cname = citem["item_name"]
                if rstem and len(rstem) >= 2 and rstem in cname \
                        and re.search(r"(血症|病|症|炎|瘤|癌|肿|硬化|息肉|结石|囊肿|异常)$", cname):
                    continue
                # 2026-09-05: 综合征诊断("血脂异常")不以指标名作子串,
                # 只要不精确同名即放行(德宏 总检异常"血脂异常"需展示)
                if re.search(r"(血症|异常)$", cname) and cname not in reg_anomaly_names:
                    continue
                dm_matched = True
                break
        if dm_matched:
            continue

        kept.append(citem)

    # Rule 2: 结论异常 vs 绿色指标同名 → 以结论为准，剔除绿色指标项
    kept_names = {it["item_name"] for it in kept}
    regular_items = [it for it in regular_items
                     if not (it["item_name"] in kept_names and it.get("color_level") == "green")]

    # === 2026-09-09: 总检异常 ↔ conclusion_text 原文行映射 ===
    # 结论条目来自总检建议段(conclusion_text), 展示时附加 origin_row/origin_line,
    # 前端可看到每条异常对应的原文句子(验收歧义消除)。
    conc = db.execute(text(
        "SELECT r.conclusion_text FROM report_interpretation j "
        "JOIN report_info r ON r.id = j.report_id WHERE j.id = :iid"
    ), {"iid": interpretation_id}).scalar()
    if conc:
        conc_lines = (conc or "").split("\n")
        for it in regular_items + kept:
            if it.get("source") != "conclusion":
                continue
            name = (it.get("item_name") or "").strip()
            row = None
            if name and len(name) >= 2 and name in conc:
                for i, ln in enumerate(conc_lines):
                    if name in ln:
                        row = i
                        break
            it["origin_row"] = row
            it["origin_line"] = (_matched_sentence(conc_lines[row], name)[:200]
                                 if row is not None else None)

    return regular_items + kept


def _matched_sentence(line: str, name: str) -> str:
    """取结论原文行中**含该条目名的整句**(按句读切分) —— 前端红区命中句标红定位,
    比整行更精准(仁济/中医院结论行常含多句)。"""
    if not line:
        return ""
    for sent in re.split(r"(?<=[。！？；;])", line):
        if name in sent:
            return sent.strip()
    return line.strip()


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
