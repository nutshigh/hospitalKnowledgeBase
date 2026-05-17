# AI 解读模块 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 AI 解读模块——三色规则引擎、LLM 解读生成、历年对比研判、高风险人群汇总。

**Architecture:** AI 解读模块消费报告解析模块发布的"解析完成"事件。逐指标经规则引擎判定（红/黄/绿），再结合知识库检索结果构造 Prompt 送入 LLM 生成解读文字。解读完成后发布事件供统计分析模块消费。

**Tech Stack:** FastAPI, SQLAlchemy, RabbitMQ, 本地 LLM（vLLM OpenAI 兼容 API）

---

## 文件结构

```
backend/app/
├── modules/
│   └── interpretation/
│       ├── __init__.py
│       ├── models.py           # report_interpretation, indicator_judgment, triage_rule
│       ├── schemas.py          # Pydantic
│       ├── rules_engine.py     # 三色规则引擎（核心）
│       ├── service.py          # 业务逻辑
│       ├── router.py           # 查询 API + 规则管理 API
│       └── worker.py           # RabbitMQ Worker（消费解析事件）
├── core/
│   └── llm_client.py           # LLM 调用客户端
└── main.py                     # 注册路由
```

---

### Task 1: 创建分支 + 数据模型

**Branch:** `feat/ai-interpretation` from `infra-setup`

- [ ] **Step 1: 创建分支**

```bash
git checkout infra-setup && git checkout -b feat/ai-interpretation
```

- [ ] **Step 2: 编写 ORM 模型**

`app/modules/interpretation/models.py`:
```python
from sqlalchemy import Column, BigInteger, String, Text, Integer, DateTime, ForeignKey, JSON, func
from app.models.base import Base


class ReportInterpretation(Base):
    __tablename__ = "report_interpretation"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, ForeignKey("report_info.id"), nullable=False)
    overall_level = Column(String(10), nullable=True)
    red_count = Column(Integer, nullable=False, default=0)
    yellow_count = Column(Integer, nullable=False, default=0)
    green_count = Column(Integer, nullable=False, default=0)
    summary_text = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)


class IndicatorJudgment(Base):
    __tablename__ = "indicator_judgment"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    interpretation_id = Column(BigInteger, ForeignKey("report_interpretation.id"), nullable=False)
    indicator_id = Column(BigInteger, ForeignKey("report_indicator.id"), nullable=False)
    item_name = Column(String(100), nullable=False)
    result_value = Column(String(50), nullable=True)
    deviation = Column(String(10), nullable=True)
    color_level = Column(String(10), nullable=True)
    matched_rule_id = Column(BigInteger, nullable=True)
    explanation = Column(Text, nullable=True)
    suggestion = Column(Text, nullable=True)
    knowledge_refs = Column(JSON, nullable=True)


class TriageRule(Base):
    __tablename__ = "triage_rule"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_name = Column(String(100), nullable=False)
    rule_type = Column(String(20), nullable=False)
    indicator_code = Column(String(50), nullable=True)
    conditions = Column(JSON, nullable=False)
    color_level = Column(String(10), nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
```

- [ ] **Step 3: 验证导入**

```bash
uv run python -c "from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment, TriageRule; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/interpretation/
git commit -m "feat(interpret): add ORM models"
```

---

### Task 2: Schemas + LLM 客户端

- [ ] **Step 1: 编写 schemas.py**

`app/modules/interpretation/schemas.py`:
```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class IndicatorJudgmentSchema(BaseModel):
    indicator_id: int
    item_name: str
    result_value: Optional[str] = None
    deviation: Optional[str] = None
    color_level: Optional[str] = None
    explanation: Optional[str] = None
    suggestion: Optional[str] = None


class InterpretationResponse(BaseModel):
    id: int
    report_id: int
    overall_level: Optional[str] = None
    red_count: int
    yellow_count: int
    green_count: int
    summary_text: Optional[str] = None
    status: str
    indicators: List[IndicatorJudgmentSchema] = []
    created_at: datetime
    completed_at: Optional[datetime] = None


class HighRiskItem(BaseModel):
    user_id: int
    report_id: int
    name: Optional[str] = None
    unit_name: Optional[str] = None
    red_count: int
    main_indicators: List[str] = []


class HighRiskResponse(BaseModel):
    items: List[dict]
    total: int


class TriageRuleCreate(BaseModel):
    rule_name: str = Field(..., min_length=1, max_length=100)
    rule_type: str = Field(..., pattern="^(value_range|key_indicator|combo|trend)$")
    indicator_code: Optional[str] = None
    conditions: dict
    color_level: str = Field(..., pattern="^(red|yellow|green)$")
    priority: int = 0


class TriageRuleUpdate(BaseModel):
    rule_name: Optional[str] = None
    rule_type: Optional[str] = None
    indicator_code: Optional[str] = None
    conditions: Optional[dict] = None
    color_level: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[int] = None


class TriageRuleResponse(BaseModel):
    id: int
    rule_name: str
    rule_type: str
    indicator_code: Optional[str] = None
    conditions: dict
    color_level: str
    priority: int
    is_active: int
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 2: 编写 LLM 客户端**

`app/core/llm_client.py`:
```python
from httpx import Client, Timeout

INTERPRETATION_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，
为体检者撰写易懂的指标解读和健康建议。

规则:
1. 绿区指标一笔带过，重点解读红区和黄区
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统的"注意饮食"
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"
"""


class LLMClient:
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.client = Client(timeout=Timeout(120.0))

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": "qwen2.5",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"]

    def interpret_indicator(self, indicator: dict, knowledge_context: str) -> dict:
        prompt = f"""## 本次报告数据
| 指标 | 结果 | 参考区间 | 判定 |
|------|------|----------|------|
| {indicator.get('item_name', '')} | {indicator.get('result_value', '')} | {indicator.get('ref_range_low', '')}-{indicator.get('ref_range_high', '')} | {indicator.get('deviation', '')}({indicator.get('color_level', '')}) |

## 参考知识库
{knowledge_context if knowledge_context else '无相关知识库条目'}

请解读这个指标，给出健康建议。"""
        return self.generate(INTERPRETATION_SYSTEM_PROMPT, prompt)

    def generate_summary(self, report_summary: str, knowledge_context: str) -> str:
        prompt = f"""## 报告概况
{report_summary}

## 参考知识库
{knowledge_context if knowledge_context else '无相关知识库条目'}

请生成综合健康小结。"""
        return self.generate(INTERPRETATION_SYSTEM_PROMPT, prompt)


llm_client = LLMClient()
```

- [ ] **Step 3: 验证导入**

```bash
uv run python -c "from app.modules.interpretation.schemas import InterpretationResponse; from app.core.llm_client import llm_client; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/interpretation/schemas.py app/core/llm_client.py
git commit -m "feat(interpret): add schemas and LLM client"
```

---

### Task 3: 三色规则引擎

- [ ] **Step 1: 编写 rules_engine.py**

`app/modules/interpretation/rules_engine.py`:
```python
from typing import List, Optional, Dict
from dataclasses import dataclass, field


@dataclass
class RuleResult:
    color_level: str  # red / yellow / green
    deviation: str    # normal / high / low / critical
    matched_rule_id: Optional[int] = None
    matched_rule_name: str = ""


class RulesEngine:
    def __init__(self):
        self._rules_cache: Dict[str, List[dict]] = {}

    def load_rules(self, hospital_id: str, rules: List[dict]):
        self._rules_cache[hospital_id] = sorted(rules, key=lambda r: r.get("priority", 0))

    def evaluate(self, hospital_id: str, indicator: dict, history: List[dict] = None) -> RuleResult:
        rules = self._rules_cache.get(hospital_id, [])
        rules = [r for r in rules if r.get("is_active", 1)]
        result = RuleResult(color_level="green", deviation="normal")

        for rule in rules:
            rule_type = rule.get("rule_type")
            conditions = rule.get("conditions", {})

            if rule_type == "value_range":
                if self._match_value_range(indicator, conditions):
                    result = self._upgrade(result, rule, conditions)
            elif rule_type == "key_indicator":
                if self._match_key_indicator(indicator, conditions, rule):
                    result = self._upgrade(result, rule, conditions)
            elif rule_type == "combo":
                pass  # Combo rules need multiple indicators — handled at report level
            elif rule_type == "trend":
                if history and self._match_trend(indicator, history, conditions):
                    result = self._upgrade(result, rule, conditions)

        return result

    def _match_value_range(self, indicator: dict, conditions: dict) -> bool:
        try:
            value = float(indicator.get("result_value", 0))
        except (ValueError, TypeError):
            return False  # Non-numeric indicators can't be matched by value_range

        op = conditions.get("op", "gt")
        threshold = float(conditions.get("value", 0))
        multiplier = float(conditions.get("multiplier", 1))

        ref_high = indicator.get("ref_range_high")
        ref_low = indicator.get("ref_range_low")

        # Check against reference range
        if ref_high and ref_low:
            try:
                ref_high_f = float(ref_high)
                ref_low_f = float(ref_low)
                if value > ref_high_f * multiplier:
                    return True
                if value < ref_low_f * multiplier:
                    return True
            except (ValueError, TypeError):
                pass

        # Check against absolute threshold
        if op == "gt" and value > threshold:
            return True
        if op == "gte" and value >= threshold:
            return True
        if op == "lt" and value < threshold:
            return True
        return False

    def _match_key_indicator(self, indicator: dict, conditions: dict, rule: dict) -> bool:
        indicator_code = rule.get("indicator_code", "")
        item_name = indicator.get("item_name", "").strip()
        item_standard = indicator.get("item_name_standard", "").strip() if indicator.get("item_name_standard") else ""
        return indicator_code in item_name or indicator_code in item_standard

    def _match_trend(self, indicator: dict, history: List[dict], conditions: dict) -> bool:
        if len(history) < 2:
            return False
        values = []
        for h in history:
            try:
                values.append(float(h.get("result_value", 0)))
            except (ValueError, TypeError):
                continue
        if len(values) < 2:
            return False

        # Check if values are continuously worsening (moving away from normal range)
        ref_high = indicator.get("ref_range_high")
        if ref_high:
            try:
                ref = float(ref_high)
                worsening = all(v > ref for v in values) and values[-1] > values[0]
                return worsening
            except (ValueError, TypeError):
                pass
        return False

    def _upgrade(self, current: RuleResult, rule: dict, conditions: dict) -> RuleResult:
        new_level = rule.get("color_level", current.color_level)
        if _level_rank(new_level) > _level_rank(current.color_level):
            deviation = "critical" if conditions.get("critical", False) else _infer_deviation(conditions, current.deviation)
            return RuleResult(
                color_level=new_level, deviation=deviation,
                matched_rule_id=rule.get("id"), matched_rule_name=rule.get("rule_name", ""),
            )
        return current

    def evaluate_report(self, hospital_id: str, indicators: List[dict], history_map: Dict[str, List[dict]] = None) -> List[RuleResult]:
        history_map = history_map or {}
        results = []
        for ind in indicators:
            item_key = ind.get("item_name_standard") or ind.get("item_name", "")
            history = history_map.get(item_key, [])
            results.append(self.evaluate(hospital_id, ind, history))
        return results


def _level_rank(level: str) -> int:
    return {"green": 0, "yellow": 1, "red": 2}.get(level, 0)


def _infer_deviation(conditions: dict, fallback: str) -> str:
    op = conditions.get("op", "")
    if op in ("gt", "gte"):
        return "high"
    if op in ("lt", "lte"):
        return "low"
    return fallback


rules_engine = RulesEngine()
```

- [ ] **Step 2: 验证导入**

```bash
uv run python -c "from app.modules.interpretation.rules_engine import rules_engine, RulesEngine; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add app/modules/interpretation/rules_engine.py
git commit -m "feat(interpret): add triage rules engine"
```

---

### Task 4: 业务逻辑层

- [ ] **Step 1: 编写 service.py**

`app/modules/interpretation/service.py`:
```python
from typing import Optional, List
from sqlalchemy.orm import Session

from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment, TriageRule
from app.modules.interpretation.rules_engine import rules_engine
from app.modules.interpretation.schemas import TriageRuleCreate, TriageRuleUpdate
from app.modules.report.models import ReportInfo, ReportIndicator
from app.core.llm_client import llm_client
from app.core.rabbitmq import rabbitmq, TaskMessage
import httpx


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


# ---- Interpretation ----

def process_interpretation(db: Session, report_id: int, hospital_id: str):
    """Main pipeline: called by worker when report_parsed event received."""
    report = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not report:
        return

    interp = ReportInterpretation(report_id=report_id, status="processing")
    db.add(interp)
    db.commit()
    db.refresh(interp)

    try:
        indicators = db.query(ReportIndicator).filter(ReportIndicator.report_id == report_id).all()

        # Load rules
        rules = list_rules(db)
        rules_engine.load_rules(hospital_id, [{
            "id": r.id, "rule_name": r.rule_name, "rule_type": r.rule_type,
            "indicator_code": r.indicator_code, "conditions": r.conditions,
            "color_level": r.color_level, "priority": r.priority, "is_active": r.is_active,
        } for r in rules])

        # Evaluate each indicator
        red_count = yellow_count = green_count = 0
        judgments = []

        for ind in indicators:
            ind_dict = {
                "item_name": ind.item_name,
                "item_name_standard": ind.item_name_standard,
                "result_value": ind.result_value,
                "unit": ind.unit,
                "ref_range_low": ind.ref_range_low,
                "ref_range_high": ind.ref_range_high,
            }
            result = rules_engine.evaluate(hospital_id, ind_dict)

            # Derive deviation from result if normal
            deviation = result.deviation
            if deviation == "normal" and ind.ref_range_high and ind.result_value:
                try:
                    val = float(ind.result_value)
                    ref_high = float(ind.ref_range_high)
                    ref_low = float(ind.ref_range_low) if ind.ref_range_low else 0
                    if val > ref_high:
                        deviation = "high"
                    elif val < ref_low:
                        deviation = "low"
                except (ValueError, TypeError):
                    pass

            # Get explanation from LLM for non-green indicators
            explanation = ""
            suggestion = ""
            if result.color_level != "green":
                try:
                    knowledge_context = _fetch_knowledge(hospital_id, ind.item_name, ind.result_value)
                    response = llm_client.interpret_indicator(
                        {**ind_dict, "deviation": deviation, "color_level": result.color_level},
                        knowledge_context,
                    )
                    explanation = response
                    suggestion = response
                except Exception:
                    pass

            j = IndicatorJudgment(
                interpretation_id=interp.id, indicator_id=ind.id,
                item_name=ind.item_name, result_value=ind.result_value,
                deviation=deviation, color_level=result.color_level,
                matched_rule_id=result.matched_rule_id,
                explanation=explanation, suggestion=suggestion,
            )
            db.add(j)

            if result.color_level == "red":
                red_count += 1
            elif result.color_level == "yellow":
                yellow_count += 1
            else:
                green_count += 1

        db.commit()

        # Determine overall level
        overall = "green"
        if red_count > 0:
            overall = "red"
        elif yellow_count > 0:
            overall = "yellow"

        interp.red_count = red_count
        interp.yellow_count = yellow_count
        interp.green_count = green_count
        interp.overall_level = overall
        interp.status = "completed"
        interp.completed_at = __import__("datetime").datetime.utcnow()
        db.commit()

        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=0,
            payload={"event": "interpretation_done", "report_id": report_id, "hospital_id": hospital_id},
        ))

    except Exception as e:
        interp.retry_count += 1
        interp.status = "failed" if interp.retry_count >= 3 else "pending"
        db.commit()


def _fetch_knowledge(hospital_id: str, item_name: str, result_value: str) -> str:
    """Fetch knowledge context from knowledge module's internal API."""
    try:
        query = f"{item_name} {result_value}"
        response = httpx.post(
            "http://localhost:8000/api/v1/knowledge/internal/search",
            json={"hospital_id": hospital_id, "query": query, "top_k": 3},
            timeout=10.0,
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                return "\n".join(f"[{r['title']}] {r['content']}" for r in results)
    except Exception:
        pass
    return ""


def get_interpretation(db: Session, report_id: int) -> Optional[ReportInterpretation]:
    return db.query(ReportInterpretation).filter(ReportInterpretation.report_id == report_id).first()


def get_judgments(db: Session, interpretation_id: int) -> List[IndicatorJudgment]:
    return db.query(IndicatorJudgment).filter(IndicatorJudgment.interpretation_id == interpretation_id).all()


def get_high_risk_list(db: Session, hospital_id: str) -> List[dict]:
    """Get all red-zone reports in a hospital."""
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
```

- [ ] **Step 2: 编写 worker.py**

`app/modules/interpretation/worker.py`:
```python
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq
from app.modules.interpretation.service import process_interpretation


def handle_interpretation_task(message: dict):
    payload = message.get("payload", {})
    report_id = payload.get("report_id")
    hospital_id = payload.get("hospital_id")

    if not report_id:
        return

    db = next(get_hospital_db(hospital_id))
    try:
        process_interpretation(db, report_id, hospital_id)
    finally:
        db.close()


def start_worker():
    rabbitmq.consume("interpretation.urgent", handle_interpretation_task)
    rabbitmq.consume("interpretation.normal", handle_interpretation_task)
    print("Interpretation worker started, waiting for tasks...")
    rabbitmq.start_consuming()
```

- [ ] **Step 3: 验证导入**

```bash
uv run python -c "from app.modules.interpretation.service import process_interpretation, list_rules; from app.modules.interpretation.worker import start_worker; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/interpretation/service.py app/modules/interpretation/worker.py
git commit -m "feat(interpret): add business logic service and worker"
```

---

### Task 5: REST API 路由

- [ ] **Step 1: 编写 router.py**

`app/modules/interpretation/router.py`:
```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_hospital_db
from app.middleware.hospital_context import get_current_hospital_id
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.interpretation import schemas, service

router = APIRouter()


def _get_hospital_id() -> str:
    hid = get_current_hospital_id()
    if not hid:
        raise ValidationException(detail="Hospital context required")
    return hid


def _get_db(hospital_id: str = Depends(_get_hospital_id)):
    return next(get_hospital_db(hospital_id))


# ---- Interpretation queries ----

@router.get("/{report_id}", response_model=schemas.InterpretationResponse)
def get_interpretation(report_id: int, db: Session = Depends(_get_db)):
    interp = service.get_interpretation(db, report_id)
    if not interp:
        raise NotFoundException(detail="Interpretation not found")
    judgments = service.get_judgments(db, interp.id)
    return {
        "id": interp.id, "report_id": interp.report_id,
        "overall_level": interp.overall_level,
        "red_count": interp.red_count, "yellow_count": interp.yellow_count,
        "green_count": interp.green_count, "summary_text": interp.summary_text,
        "status": interp.status,
        "indicators": [
            {"indicator_id": j.indicator_id, "item_name": j.item_name,
             "result_value": j.result_value, "deviation": j.deviation,
             "color_level": j.color_level, "explanation": j.explanation,
             "suggestion": j.suggestion}
            for j in judgments
        ],
        "created_at": interp.created_at, "completed_at": interp.completed_at,
    }


@router.get("/{report_id}/indicators")
def get_judgments(report_id: int, db: Session = Depends(_get_db)):
    interp = service.get_interpretation(db, report_id)
    if not interp:
        raise NotFoundException(detail="Interpretation not found")
    return service.get_judgments(db, interp.id)


# ---- High risk list ----

@router.get("/high-risk/list", response_model=schemas.HighRiskResponse)
def get_high_risk_list(
    db: Session = Depends(_get_db),
    hospital_id: str = Depends(_get_hospital_id),
):
    items = service.get_high_risk_list(db, hospital_id)
    return {"items": items, "total": len(items)}


# ---- Triage rules CRUD ----

@router.get("/rules/all", response_model=list[schemas.TriageRuleResponse])
def list_rules(db: Session = Depends(_get_db)):
    return service.list_rules(db)


@router.post("/rules", response_model=schemas.TriageRuleResponse)
def create_rule(data: schemas.TriageRuleCreate, db: Session = Depends(_get_db)):
    return service.create_rule(db, data)


@router.put("/rules/{rule_id}", response_model=schemas.TriageRuleResponse)
def update_rule(rule_id: int, data: schemas.TriageRuleUpdate, db: Session = Depends(_get_db)):
    rule = service.update_rule(db, rule_id, data)
    if not rule:
        raise NotFoundException(detail="Rule not found")
    return rule


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(_get_db)):
    if not service.delete_rule(db, rule_id):
        raise NotFoundException(detail="Rule not found")
    return {"status": "deleted"}
```

- [ ] **Step 2: 注册路由到 main.py**

```python
# 添加:
from app.modules.interpretation.router import router as interpretation_router
app.include_router(interpretation_router, prefix="/api/v1/interpretations", tags=["interpretations"])
```

- [ ] **Step 3: 验证路由**

```bash
uv run python -c "from app.main import app; routes = [r.path for r in app.routes if hasattr(r, 'path')]; print([r for r in routes if 'interpret' in r or 'rule' in r])"
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/interpretation/router.py app/main.py
git commit -m "feat(interpret): add REST API routes"
```

---

### Task 6: 验证 + 推送

- [ ] **Step 1: 全量导入验证**

```bash
uv run python -c "
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment, TriageRule
from app.modules.interpretation.schemas import InterpretationResponse, TriageRuleCreate
from app.modules.interpretation.rules_engine import rules_engine
from app.modules.interpretation.service import process_interpretation, list_rules
from app.modules.interpretation.worker import start_worker
from app.modules.interpretation.router import router
from app.core.llm_client import llm_client
print('All imports OK')
"
```

- [ ] **Step 2: 服务器启动验证**

```bash
timeout 3 uv run uvicorn app.main:app --port 8003 2>&1 || true
```

Expected: `Application startup complete.`

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "chore(interpret): verify module integrity"
```

- [ ] **Step 4: 推送 + 合并**

```bash
git push -u origin feat/ai-interpretation
git checkout infra-setup && git merge feat/ai-interpretation && git push origin infra-setup
```
