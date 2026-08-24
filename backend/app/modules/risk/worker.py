"""risk.hit 队列 worker:解读完成后异步计算病种命中并固化到 disease_hit。

- 幂等:同 (report_id, disease_name) 唯一,重复投递直接更新;
- 失败:重投 retry 队列(沿用 backoff),不阻塞用户链路;
- 无 bulk 窗口限制(纯 DB 计算,无 LLM)。
"""
import json
import logging

from sqlalchemy import text

from app.core.database import get_hospital_db
from app.core.logging_config import setup_logging
from app.core.rabbitmq import rabbitmq
from app.core.retry import backoff_for_retry

from app.modules.risk.engine import compute_hits

_log = logging.getLogger("app.risk.worker")


def _load_rules(db):
    mappings = [
        {"id": r.id, "item_name_standard": r.item_name_standard,
         "disease_name": r.disease_name, "disease_category": r.disease_category,
         "disease_class": r.disease_class, "match_level": r.match_level,
         "match_deviation": r.match_deviation}
        for r in db.execute(text(
            "SELECT id, item_name_standard, disease_name, disease_category,"
            " disease_class, match_level, match_deviation FROM disease_mapping"
            " WHERE enabled=1"
        ))
    ]
    rules = []
    for r in db.execute(text(
        "SELECT id, rule_code, disease_name, disease_category, disease_class,"
        " member_items FROM disease_rule WHERE enabled=1"
    )):
        members = r.member_items
        if isinstance(members, str):
            members = json.loads(members)
        rules.append({"id": r.id, "rule_code": r.rule_code,
                      "disease_name": r.disease_name,
                      "disease_category": r.disease_category,
                      "disease_class": r.disease_class,
                      "member_items": members})
    return mappings, rules


def compute_and_store(db, report_id, interpretation_id=None):
    """对单份报告重算病种命中并写入 disease_hit(先查后插, 幂等)。返回命中数。"""
    info = db.execute(text(
        "SELECT user_id, unit_name, report_date FROM report_info WHERE id=:rid"
    ), {"rid": report_id}).first()
    if not info:
        return 0
    judgments = list(db.execute(text(
        "SELECT ij.id, ij.indicator_id, ij.item_name, ij.color_level, ij.deviation, ij.source"
        " FROM indicator_judgment ij"
        " JOIN report_interpretation ri ON ij.interpretation_id = ri.id"
        " WHERE ri.report_id = :rid AND ri.status='completed'"
    ), {"rid": report_id}))
    if not judgments:
        return 0
    std_names = {
        r[0]: r[1] for r in db.execute(text(
            "SELECT id, item_name_standard FROM report_indicator"
            " WHERE report_id=:rid AND item_name_standard IS NOT NULL"
        ), {"rid": report_id})
    }

    class J:
        def __init__(self, row):
            self.item_name = row[2]
            self.color_level = row[3]
            self.deviation = row[4]
            self.indicator_id = row[1]
            self.source = row[5]

    mappings, rules = _load_rules(db)
    hits = compute_hits([J(r) for r in judgments], std_names, mappings, rules)
    for h in hits:
        existing = db.execute(text(
            "SELECT id FROM disease_hit WHERE report_id=:rid AND disease_name=:dn"
        ), {"rid": report_id, "dn": h["disease_name"]}).first()
        if existing:
            db.execute(text(
                "UPDATE disease_hit SET hit_type=:ht, hit_items=:items,"
                " mapping_id=:mid, rule_id=:ruid, interpretation_id=:iid,"
                " disease_category=:dc, disease_class=:dk"
                " WHERE id=:id"
            ), {"ht": h["hit_type"], "items": json.dumps(h["hit_items"], ensure_ascii=False),
                "mid": h["mapping_id"], "ruid": h["rule_id"], "iid": interpretation_id,
                "dc": h["disease_category"], "dk": h["disease_class"],
                "id": existing.id})
        else:
            db.execute(text(
                "INSERT INTO disease_hit (report_id, interpretation_id, user_id, unit_name,"
                " report_date, disease_name, disease_category, disease_class, hit_type,"
                " mapping_id, rule_id, hit_items)"
                " VALUES (:rid, :iid, :uid, :un, :rd, :dn, :dc, :dk, :ht, :mid, :ruid, :items)"
            ), {"rid": report_id, "iid": interpretation_id, "uid": info.user_id,
                "un": info.unit_name, "rd": info.report_date, "dn": h["disease_name"],
                "dc": h["disease_category"], "dk": h["disease_class"],
                "ht": h["hit_type"], "mid": h["mapping_id"],
                "ruid": h["rule_id"], "items": json.dumps(h["hit_items"], ensure_ascii=False)})
    db.commit()
    return len(hits)


def handle_risk_task(message: dict):
    routing_key = message.get("_routing_key", "risk.hit")
    payload = message.get("payload", {})
    report_id = payload.get("report_id")
    hospital_id = message.get("hospital_id") or payload.get("hospital_id")
    interpretation_id = payload.get("interpretation_id")
    if not report_id or not hospital_id:
        return
    db = next(get_hospital_db(hospital_id))
    try:
        n = compute_and_store(db, report_id, interpretation_id)
        _log.info("risk ok report=%s hospital=%s hits=%d", report_id, hospital_id, n)
    except Exception as e:
        _log.warning("risk fail report=%s hospital=%s: %s", report_id, hospital_id, e)
        body = json.dumps({
            "task_type": "risk",
            "hospital_id": hospital_id,
            "payload": payload,
        }).encode()
        rabbitmq.publish_retry(routing_key, body, expiration_ms=backoff_for_retry(0))
        return
    finally:
        db.close()


def start_worker():
    setup_logging()
    while True:
        try:
            rabbitmq.consume("risk.hit", handle_risk_task)
            print("Risk engine worker started (risk.hit)")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Worker disconnected: {e}, reconnecting in 3s...")
            import time
            time.sleep(3)
