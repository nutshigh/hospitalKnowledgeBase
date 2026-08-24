"""映射/组合规则管理 CRUD(预留):供 sz-mana Java 后台调用(服务间鉴权)。

范围: 列表 + 启用/停用 + 新增本院私有规则(source=LOCAL)。
后续不同机构报告进入后, 规则可在线上填充, 不需改代码。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.service_auth import require_service_client

router = APIRouter(prefix="/risk", dependencies=[Depends(require_service_client)])


def _get_db(hospital_id: str = Depends(require_service_client)):
    gen = get_hospital_db(hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@router.get("/mappings")
def list_mappings(category: Optional[str] = Query(None),
                  db: Session = Depends(_get_db)):
    q = ("SELECT id, item_name_standard, disease_name, disease_category,"
         " disease_class, match_level, match_deviation, source, enabled, sort_code"
         " FROM disease_mapping")
    params = {}
    if category:
        q += " WHERE disease_category = :c"
        params["c"] = category
    q += " ORDER BY sort_code, id"
    rows = db.execute(text(q), params).mappings().all()
    return {"items": [dict(r) for r in rows]}


class MappingUpsert(BaseModel):
    item_name_standard: str
    disease_name: str
    disease_category: str = "CHRONIC"
    disease_class: Optional[str] = None
    match_level: str = "YELLOW"  # YELLOW|RED
    match_deviation: Optional[str] = None  # 偏高|偏低|None


@router.post("/mappings")
def upsert_mapping(req: MappingUpsert, db: Session = Depends(_get_db)):
    """新增/更新本院私有映射(按 item_name_standard upsert, 保留 CENTRAL 不动)。"""
    row = db.execute(text(
        "SELECT id FROM disease_mapping WHERE item_name_standard=:s"
    ), {"s": req.item_name_standard}).fetchone()
    if row:
        src = db.execute(text(
            "SELECT source FROM disease_mapping WHERE id=:id"
        ), {"id": row.id}).scalar()
        if src == "CENTRAL":
            return {"ok": False, "error": "CENTRAL mapping exists, use update endpoint"}
        db.execute(text(
            "UPDATE disease_mapping SET disease_name=:d, disease_category=:c,"
            " disease_class=:k, match_level=:lv, match_deviation=:dev WHERE id=:id"
        ), {"d": req.disease_name, "c": req.disease_category, "k": req.disease_class,
            "lv": req.match_level, "dev": req.match_deviation, "id": row.id})
    else:
        db.execute(text(
            "INSERT INTO disease_mapping (item_name_standard, disease_name,"
            " disease_category, disease_class, match_level, match_deviation, source)"
            " VALUES (:s, :d, :c, :k, :lv, :dev, 'LOCAL')"
        ), {"s": req.item_name_standard, "d": req.disease_name,
            "c": req.disease_category, "k": req.disease_class,
            "lv": req.match_level, "dev": req.match_deviation})
    db.commit()
    return {"ok": True}


class ToggleRequest(BaseModel):
    enabled: bool


@router.put("/mappings/{mapping_id}/enabled")
def toggle_mapping(mapping_id: int, req: ToggleRequest,
                   db: Session = Depends(_get_db)):
    db.execute(text("UPDATE disease_mapping SET enabled=:e WHERE id=:i"),
               {"e": 1 if req.enabled else 0, "i": mapping_id})
    db.commit()
    return {"ok": True}


class RuleCreate(BaseModel):
    rule_code: str
    disease_name: str
    disease_category: str = "CHRONIC"
    disease_class: Optional[str] = None
    member_items: list[dict]  # [{"name": 标准名, "min_level": "YELLOW", "deviation": 或null}]


@router.post("/rules")
def create_rule(req: RuleCreate, db: Session = Depends(_get_db)):
    import json as _json
    existing = db.execute(text(
        "SELECT id FROM disease_rule WHERE rule_code=:c"
    ), {"c": req.rule_code}).fetchone()
    if existing:
        return {"ok": False, "error": "rule_code exists"}
    db.execute(text(
        "INSERT INTO disease_rule (rule_code, disease_name, disease_category,"
        " disease_class, member_items, source)"
        " VALUES (:rc, :d, :c, :k, :mi, 'LOCAL')"
    ), {"rc": req.rule_code, "d": req.disease_name, "c": req.disease_category,
        "k": req.disease_class,
        "mi": _json.dumps(req.member_items, ensure_ascii=False)})
    db.commit()
    return {"ok": True}


@router.put("/rules/{rule_id}/enabled")
def toggle_rule(rule_id: int, req: ToggleRequest,
                db: Session = Depends(_get_db)):
    db.execute(text("UPDATE disease_rule SET enabled=:e WHERE id=:i"),
               {"e": 1 if req.enabled else 0, "i": rule_id})
    db.commit()
    return {"ok": True}


@router.get("/rules")
def list_rules(db: Session = Depends(_get_db)):
    import json as _json
    rows = db.execute(text(
        "SELECT id, rule_code, disease_name, disease_category, disease_class,"
        " member_items, source, enabled, sort_code FROM disease_rule"
        " ORDER BY sort_code, id"
    )).fetchall()
    items = []
    for r in rows:
        members = r.member_items
        if isinstance(members, str):
            try:
                members = _json.loads(members)
            except Exception:
                members = []
        items.append({
            "id": r.id, "rule_code": r.rule_code, "disease_name": r.disease_name,
            "disease_category": r.disease_category, "disease_class": r.disease_class,
            "member_items": members, "source": r.source, "enabled": r.enabled,
            "sort_code": r.sort_code,
        })
    return {"items": items}
