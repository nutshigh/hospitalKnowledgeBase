# 异常指标→慢性病/重大疾病映射与命中固化 实施计划

> **For agentic workers:** 按任务顺序执行本计划,步骤使用 checkbox (`- [ ]`) 语法跟踪。

**Goal:** 建设"异常指标→慢性病(CHRONIC)/重大疾病(MAJOR)映射"功能:LLM 基于两模板报告指标项生成候选映射(单指标+严格 AND 组合规则),人工审核后以中央静态种子落库;解读完成后由独立队列触发规则引擎,把命中结果固化到报告粒度的新表;统计端点改为直查命中表,供 sz-mana Java 后台"健康风险管理统计"使用。不用于向用户返回诊断。

**Architecture:** 三层:
1. **映射层** — `disease_mapping`(单指标精确匹配,已有表,加 `source` 列)+ 新表 `disease_rule`(严格 AND 组合规则)+ 新表 `disease_hit`(报告粒度命中固化,冗余 user_id/unit_name/report_date)
2. **引擎层** — `app/modules/risk/engine.py` 纯函数:输入报告红黄判定集合+映射/规则,输出命中行
3. **触发层** — interpretation worker 完成后 publish 新队列 `risk.hit`,独立 risk worker 消费计算(不占用户接收结果链路),失败走既有 retry/DLQ

**Tech Stack:** FastAPI + SQLAlchemy + pika(RabbitMQ topic exchange `hospital.tasks`)+ MySQL(per-tenant `hospital_*` 库)+ MedGo vLLM(仅候选生成脚本用,运行时引擎纯 DB 计算无 LLM)

**前置事实(已核实):**
- `indicator_judgment`: `interpretation_id, indicator_id, item_name, result_value, deviation, color_level(red/yellow/green), source(indicator/conclusion), ...`
- `report_indicator.item_name_standard` 为标准名(如"肌酸激酶"),`indicator_judgment.item_name` 为原始名(如"血肌酸激酶")
- `disease_service.py` 三个端点现查时 LEFT JOIN `disease_mapping` 精确匹配 `ij.item_name`;将改为查 `disease_hit`
- 两模板报告:`报告样例/体检报告示例/詹姆斯_H001_9.pdf`、`布朗尼_H002_8.pdf`(已解析入库 H001/H002,user_id 9/8)
- 测试风格:`tests/test_interp_worker_bulk.py`(sqlite 内存库 + MagicMock patch);`tests/conftest.py` 设 `IS_TESTING=true`
- 队列注册 `app/core/rabbitmq.py::QUEUES`/`RETRY_QUEUES`;worker 消费模式见 `interpretation/worker.py`
- venv 注意事项见 AGENTS.md(vllm 独立 venv、主 venv cu126、pin 版本)
- 12 项需求决策已闭环(见本文件末尾 Self-Review 对照表)

---

### Task 1: 数据模型与 DDL(新表 + source 列)

**Files:**
- Create: `backend/app/modules/risk/__init__.py`
- Create: `backend/app/modules/risk/models.py`
- Modify: `start.sh`(DDL 块,disease_mapping 之后)
- Test: `backend/tests/test_risk_models.py`

- [ ] **Step 1: 写模型文件**

```python
# backend/app/modules/risk/__init__.py
# (空文件)
```

```python
# backend/app/modules/risk/models.py
from sqlalchemy import BigInteger, Column, Date, DateTime, Integer, JSON, String, func
from app.models.base import Base


class DiseaseRule(Base):
    __tablename__ = "disease_rule"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_code = Column(String(50), nullable=False, unique=True)
    disease_name = Column(String(100), nullable=False)
    disease_category = Column(String(20), nullable=False, default="CHRONIC")  # CHRONIC|MAJOR
    disease_class = Column(String(100), nullable=True)
    member_items = Column(JSON, nullable=False)  # ["收缩压","舒张压"] 严格AND:全部成员须异常
    source = Column(String(20), nullable=False, default="LOCAL")  # CENTRAL|LOCAL
    enabled = Column(Integer, nullable=False, default=1)
    sort_code = Column(Integer, default=200)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())


class DiseaseHit(Base):
    __tablename__ = "disease_hit"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, nullable=False)
    interpretation_id = Column(BigInteger, nullable=True)
    user_id = Column(BigInteger, nullable=False)
    unit_name = Column(String(100), nullable=True)
    report_date = Column(Date, nullable=True)
    disease_name = Column(String(100), nullable=False)
    disease_category = Column(String(20), nullable=False, default="CHRONIC")
    disease_class = Column(String(100), nullable=True)
    hit_type = Column(String(20), nullable=False, default="single")  # single|combo
    mapping_id = Column(BigInteger, nullable=True)
    rule_id = Column(BigInteger, nullable=True)
    hit_items = Column(JSON, nullable=True)  # ["收缩压","舒张压"]
    created_at = Column(DateTime, server_default=func.now())
```

- [ ] **Step 2: start.sh 加 DDL(保持同风格一行一条 CREATE TABLE IF NOT EXISTS)**

在 `disease_mapping` 的 CREATE TABLE 之后追加:

```sql
CREATE TABLE IF NOT EXISTS disease_rule (id BIGINT AUTO_INCREMENT PRIMARY KEY, rule_code VARCHAR(50) NOT NULL, disease_name VARCHAR(100) NOT NULL, disease_category VARCHAR(20) DEFAULT 'CHRONIC', disease_class VARCHAR(100) DEFAULT NULL, member_items JSON DEFAULT NULL, source VARCHAR(20) DEFAULT 'LOCAL', enabled TINYINT DEFAULT 1, sort_code INT DEFAULT 200, create_time DATETIME DEFAULT CURRENT_TIMESTAMP, update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_rule_code (rule_code)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS disease_hit (id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, interpretation_id BIGINT DEFAULT NULL, user_id BIGINT NOT NULL, unit_name VARCHAR(100) DEFAULT NULL, report_date DATE DEFAULT NULL, disease_name VARCHAR(100) NOT NULL, disease_category VARCHAR(20) DEFAULT 'CHRONIC', disease_class VARCHAR(100) DEFAULT NULL, hit_type VARCHAR(20) DEFAULT 'single', mapping_id BIGINT DEFAULT NULL, rule_id BIGINT DEFAULT NULL, hit_items JSON DEFAULT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uk_report_disease (report_id, disease_name), KEY idx_user_date (user_id, report_date), KEY idx_disease (disease_name)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
ALTER TABLE disease_mapping ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'LOCAL';
```

- [ ] **Step 3: 为现有 tenant 跑增量 DDL**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend
.venv/bin/python - <<'EOF'
from app.core.database import get_session
from sqlalchemy import text
DDL = [
  "CREATE TABLE IF NOT EXISTS disease_rule (id BIGINT AUTO_INCREMENT PRIMARY KEY, rule_code VARCHAR(50) NOT NULL, disease_name VARCHAR(100) NOT NULL, disease_category VARCHAR(20) DEFAULT 'CHRONIC', disease_class VARCHAR(100) DEFAULT NULL, member_items JSON DEFAULT NULL, source VARCHAR(20) DEFAULT 'LOCAL', enabled TINYINT DEFAULT 1, sort_code INT DEFAULT 200, create_time DATETIME DEFAULT CURRENT_TIMESTAMP, update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_rule_code (rule_code)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
  "CREATE TABLE IF NOT EXISTS disease_hit (id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, interpretation_id BIGINT DEFAULT NULL, user_id BIGINT NOT NULL, unit_name VARCHAR(100) DEFAULT NULL, report_date DATE DEFAULT NULL, disease_name VARCHAR(100) NOT NULL, disease_category VARCHAR(20) DEFAULT 'CHRONIC', disease_class VARCHAR(100) DEFAULT NULL, hit_type VARCHAR(20) DEFAULT 'single', mapping_id BIGINT DEFAULT NULL, rule_id BIGINT DEFAULT NULL, hit_items JSON DEFAULT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uk_report_disease (report_id, disease_name), KEY idx_user_date (user_id, report_date), KEY idx_disease (disease_name)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
  "ALTER TABLE disease_mapping ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'LOCAL'",
]
for hid in ["H001", "H002", "H003", "H004"]:
    db = get_session(f"hospital_{hid}")
    for sql in DDL:
        db.execute(text(sql))
    db.commit(); db.close()
    print(f"{hid} OK")
EOF
```

- [ ] **Step 4: 写测试(模型可建表)**

```python
# backend/tests/test_risk_models.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.risk.models import DiseaseRule, DiseaseHit  # noqa: F401


def test_risk_tables_create():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    r = DiseaseRule(rule_code="R1", disease_name="高血压", member_items=["收缩压", "舒张压"])
    s.add(r)
    s.commit()
    assert s.query(DiseaseRule).count() == 1
    hit = DiseaseHit(report_id=1, user_id=10, disease_name="高血压", hit_items=["收缩压", "舒张压"])
    s.add(hit)
    s.commit()
    assert s.query(DiseaseHit).count() == 1
```

- [ ] **Step 5: 跑测试**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_risk_models.py -v
```

Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/risk backend/start.sh backend/tests/test_risk_models.py
git commit -m "feat(risk): disease_rule/disease_hit models + DDL + mapping source column"
```

---

### Task 2: 规则引擎纯函数

**Files:**
- Create: `backend/app/modules/risk/engine.py`
- Test: `backend/tests/test_risk_engine.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_risk_engine.py
from app.modules.risk.engine import compute_hits


def make_j(name, level, iid):
    class J:
        item_name = name
        color_level = level
        indicator_id = iid
    return J()


STD = {
    1: "肌酸激酶",
    2: "低密度脂蛋白胆固醇",
    3: "收缩压",
    4: "舒张压",
    5: "空腹血糖（GLU）",
    6: "超氧化物歧化酶",
}


def test_single_hit_matches_standard_name():
    mappings = [{"id": 1, "item_name_standard": "肌酸激酶", "disease_name": "横纹肌溶解",
                 "disease_category": "CHRONIC", "disease_class": "心血管系统"}]
    judgments = [make_j("血肌酸激酶", "yellow", 1)]
    hits = compute_hits(judgments, STD, mappings, [])
    assert hits == [{"disease_name": "横纹肌溶解", "disease_category": "CHRONIC",
                     "disease_class": "心血管系统", "hit_type": "single",
                     "mapping_id": 1, "rule_id": None, "hit_items": ["肌酸激酶"]}]


def test_combo_requires_all_members():
    rules = [{"id": 10, "rule_code": "R1", "disease_name": "高血压",
              "disease_category": "CHRONIC", "disease_class": "心血管系统",
              "member_items": ["收缩压", "舒张压"]}]
    j1 = [make_j("收缩压", "yellow", 3)]
    assert compute_hits(j1, STD, [], rules) == []
    j2 = j1 + [make_j("舒张压", "red", 4)]
    hits = compute_hits(j2, STD, [], rules)
    assert hits == [{"disease_name": "高血压", "disease_category": "CHRONIC",
                     "disease_class": "心血管系统", "hit_type": "combo",
                     "mapping_id": None, "rule_id": 10, "hit_items": ["收缩压", "舒张压"]}]


def test_green_ignored():
    mappings = [{"id": 2, "item_name_standard": "低密度脂蛋白胆固醇", "disease_name": "血脂异常",
                 "disease_category": "CHRONIC", "disease_class": "内分泌代谢"}]
    judgments = [make_j("低密度脂蛋白胆固醇", "green", 2)]
    assert compute_hits(judgments, STD, mappings, []) == []


def test_same_disease_merged_combo_wins():
    mappings = [{"id": 3, "item_name_standard": "肌酸激酶", "disease_name": "横纹肌溶解",
                 "disease_category": "CHRONIC", "disease_class": "心血管系统"}]
    rules = [{"id": 11, "rule_code": "R2", "disease_name": "横纹肌溶解",
              "disease_category": "CHRONIC", "disease_class": "心血管系统",
              "member_items": ["肌酸激酶", "超氧化物歧化酶"]}]
    j = [make_j("血肌酸激酶", "yellow", 1), make_j("超氧化物歧化酶", "yellow", 6)]
    hits = compute_hits(j, STD, mappings, rules)
    assert len(hits) == 1
    assert hits[0]["hit_type"] == "combo"
    assert set(hits[0]["hit_items"]) == {"肌酸激酶", "超氧化物歧化酶"}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_risk_engine.py -v
```

Expected: FAIL(ModuleNotFoundError: No module named 'app.modules.risk.engine')

- [ ] **Step 3: 实现引擎**

```python
# backend/app/modules/risk/engine.py
"""规则引擎纯函数:报告的红黄判定集合 → 病种命中列表。

匹配口径:
- 单指标: mapping.item_name_standard 与判定原始名 item_name 或指标标准名
  item_name_standard 任一相等即命中;
- 组合: disease_rule.member_items 每个成员都在异常名集合中(严格 AND);
- 同报告同病种合并为一行,combo 优先于 single,hit_items 合并。
"""


def _abnormal_name_set(judgments, indicator_std_names):
    names = set()
    for j in judgments:
        if j.color_level in ("red", "yellow"):
            names.add(j.item_name)
            std = indicator_std_names.get(j.indicator_id)
            if std:
                names.add(std)
    return names


def compute_hits(judgments, indicator_std_names, mappings, rules):
    abnormal = _abnormal_name_set(judgments, indicator_std_names)
    hits = {}  # disease_name -> hit dict

    def merge(name, category, klass, hit_type, mapping_id, rule_id, items):
        if name not in hits:
            hits[name] = {
                "disease_name": name, "disease_category": category,
                "disease_class": klass, "hit_type": hit_type,
                "mapping_id": mapping_id, "rule_id": rule_id,
                "hit_items": list(items),
            }
            return
        cur = hits[name]
        cur["hit_items"] = sorted(set(cur["hit_items"]) | set(items))
        if hit_type == "combo" and cur["hit_type"] != "combo":
            cur["hit_type"] = "combo"
            cur["rule_id"] = rule_id

    for m in mappings:
        if m["item_name_standard"] in abnormal:
            merge(m["disease_name"], m["disease_category"], m.get("disease_class"),
                  "single", m["id"], None, [m["item_name_standard"]])

    for r in rules:
        if all(item in abnormal for item in r["member_items"]):
            merge(r["disease_name"], r["disease_category"], r.get("disease_class"),
                  "combo", None, r["id"], r["member_items"])

    return list(hits.values())
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_risk_engine.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/risk/engine.py backend/tests/test_risk_engine.py
git commit -m "feat(risk): pure rule engine (single exact match + strict AND combo)"
```

---

### Task 3: 中央标准种子数据(静态文件 + 同步函数)

**Files:**
- Create: `backend/app/modules/risk/seed.py`
- Test: `backend/tests/test_risk_seed.py`

- [ ] **Step 1: 写种子数据与同步函数(ORM 实现,兼容 sqlite 测试)**

```python
# backend/app/modules/risk/seed.py
"""中央标准映射种子(CENTRAL source)。人工审核后的权威清单在此维护。

同步函数幂等:按 item_name_standard / rule_code upsert,不删除已有行。
基础清单取自 H001 disease_mapping 已验证的 CHRONIC/MAJOR 映射;
LLM 候选审核通过后追加。
"""
from sqlalchemy import text

CENTRAL_MAPPINGS = [
    # (item_name_standard, disease_name, category, class, sort_code)
    ("收缩压", "高血压", "CHRONIC", "心血管系统", 1),
    ("舒张压", "高血压", "CHRONIC", "心血管系统", 2),
    ("空腹血糖（GLU）", "糖尿病", "CHRONIC", "内分泌代谢", 4),
    ("糖化血红蛋白", "糖尿病", "CHRONIC", "内分泌代谢", 5),
    ("全血糖化血红蛋白测定", "糖尿病", "CHRONIC", "内分泌代谢", 6),
    ("总胆固醇（TC）", "血脂异常", "CHRONIC", "内分泌代谢", 7),
    ("甘油三酯（TG）", "血脂异常", "CHRONIC", "内分泌代谢", 8),
    ("低密度脂蛋白胆固醇（LDL-C）", "血脂异常", "CHRONIC", "内分泌代谢", 9),
    ("小而密低密度脂蛋白胆固醇", "血脂异常", "CHRONIC", "内分泌代谢", 10),
    ("高密度脂蛋白胆固醇（HDL-C）", "血脂异常", "CHRONIC", "内分泌代谢", 11),
    ("尿酸（UA）", "高尿酸血症", "CHRONIC", "内分泌代谢", 12),
    ("体质指数", "超重", "CHRONIC", "内分泌代谢", 13),
    ("甲胎蛋白", "恶性肿瘤(疑似)", "MAJOR", "肿瘤", 30),
    ("癌胚抗原", "恶性肿瘤(疑似)", "MAJOR", "肿瘤", 31),
    ("癌抗原CA19-9", "恶性肿瘤(疑似)", "MAJOR", "肿瘤", 32),
    ("前列腺特异性抗原", "恶性肿瘤(疑似)", "MAJOR", "肿瘤", 33),
    ("游离前列腺特异性抗原", "恶性肿瘤(疑似)", "MAJOR", "肿瘤", 34),
    ("肿瘤特异生长因子", "恶性肿瘤(疑似)", "MAJOR", "肿瘤", 35),
]

CENTRAL_RULES = [
    # (rule_code, disease_name, category, class, member_items, sort_code)
    ("C-HT", "高血压", "CHRONIC", "心血管系统", ["收缩压", "舒张压"], 1),
    ("C-DM", "糖尿病", "CHRONIC", "内分泌代谢",
     ["空腹血糖（GLU）", "糖化血红蛋白"], 2),
]


def sync_central(db):
    """把 CENTRAL 数据 upsert 进该 tenant 库(保留已有 LOCAL 行)。"""
    from app.modules.risk.models import DiseaseRule

    for item_std, dname, cat, klass, sort in CENTRAL_MAPPINGS:
        row = db.execute(text(
            "SELECT id FROM disease_mapping WHERE item_name_standard=:s"
        ), {"s": item_std}).fetchone()
        if row:
            db.execute(text(
                "UPDATE disease_mapping SET disease_name=:d, disease_category=:c,"
                " disease_class=:k, sort_code=:o, source='CENTRAL' WHERE id=:id"
            ), {"d": dname, "c": cat, "k": klass, "o": sort, "id": row.id})
        else:
            db.execute(text(
                "INSERT INTO disease_mapping (item_name_standard, disease_name,"
                " disease_category, disease_class, sort_code, source)"
                " VALUES (:s, :d, :c, :k, :o, 'CENTRAL')"
            ), {"s": item_std, "d": dname, "c": cat, "k": klass, "o": sort})
    for code, dname, cat, klass, members, sort in CENTRAL_RULES:
        existing = db.query(DiseaseRule).filter(DiseaseRule.rule_code == code).first()
        if existing:
            existing.disease_name = dname
            existing.disease_category = cat
            existing.disease_class = klass
            existing.member_items = members
            existing.sort_code = sort
            existing.source = "CENTRAL"
        else:
            db.add(DiseaseRule(rule_code=code, disease_name=dname,
                               disease_category=cat, disease_class=klass,
                               member_items=members, sort_code=sort,
                               source="CENTRAL"))
    db.commit()
```

- [ ] **Step 2: 写测试(同步幂等)**

```python
# backend/tests/test_risk_seed.py
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.risk.models import DiseaseRule, DiseaseHit  # noqa: F401
from app.modules.risk.seed import CENTRAL_RULES, sync_central


def test_sync_central_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    sync_central(s)
    n1 = s.execute(text("SELECT COUNT(*) FROM disease_rule")).scalar()
    sync_central(s)
    n2 = s.execute(text("SELECT COUNT(*) FROM disease_rule")).scalar()
    assert n1 == n2 == len(CENTRAL_RULES)
```

- [ ] **Step 3: 跑测试**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_risk_seed.py -v
```

Expected: 1 passed

- [ ] **Step 4: 同步到全部 tenant**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python - <<'EOF'
from app.core.database import get_session
from app.modules.risk.seed import sync_central
for hid in ["H001", "H002", "H003", "H004"]:
    db = get_session(f"hospital_{hid}")
    sync_central(db)
    db.close()
    print(f"{hid} synced")
EOF
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/risk/seed.py backend/tests/test_risk_seed.py
git commit -m "feat(risk): central seed mappings/rules + idempotent sync"
```

---

### Task 4: LLM 候选生成脚本(一次性 CLI,产出人工审核文件)

**Files:**
- Create: `backend/app/modules/risk/gen_candidates.py`
- Create: `backend/app/modules/risk/candidates/`(产物目录)

- [ ] **Step 1: 写脚本**

```python
# backend/app/modules/risk/gen_candidates.py
"""一次性 CLI:把两模板报告全部指标项喂给 MedGo,生成 指标→病种 候选映射
(单指标 + 组合规则),输出 JSON 供人工审核。

用法:
  cd backend && .venv/bin/python -m app.modules.risk.gen_candidates \
      --output app/modules/risk/candidates/llm-candidates-2026-08-14.json
"""
import argparse
import asyncio
import json
import os

from sqlalchemy import text

from app.core.database import get_session
from app.ai.llm import get_chat_model

PROMPT_TEMPLATE = """你是体检报告解读专家。以下是两份体检报告模板中的全部检验指标项清单。
请为每个"异常时可能指向慢性病(CHRONIC)或重大疾病(MAJOR)"的指标,生成
"指标名→疾病名"候选映射;对需要多个指标同时异常才能指向的病种,生成严格组合规则(全部成员须同时异常)。

要求:
1. 疾病命名用标准疾病名(如"高血压""糖尿病""恶性肿瘤(疑似)""脑卒中"),重大疾病天然是筛查疑似性质。
2. disease_category 取值: CHRONIC(慢性病) 或 MAJOR(重大疾病)。
3. disease_class 用系统分类: 心血管系统/内分泌代谢/消化系统/呼吸系统/泌尿系统/肿瘤/神经系统/其他。
4. 组合规则仅在确有医学依据时生成(如 收缩压+舒张压→高血压),rule_code 用 C- 前缀。
5. 指标名用清单中的原始名,不要改写。
6. 只输出 JSON,不要其他文字。

指标项清单:
{indicators}

输出 JSON 格式:
{{"single": [{{"item_name": "指标名", "disease_name": "疾病名", "disease_category": "CHRONIC", "disease_class": "分类"}}], "combos": [{{"rule_code": "C-XX", "disease_name": "疾病名", "disease_category": "CHRONIC", "disease_class": "分类", "member_items": ["指标1", "指标2"]}}]}}
"""


async def generate(indicators):
    model = get_chat_model()
    prompt = PROMPT_TEMPLATE.format(indicators="\n".join(f"- {i}" for i in indicators))
    resp = await model.ainvoke(prompt)
    content = resp.content
    content = content[content.find("{"):content.rfind("}") + 1]
    return json.loads(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    names = set()
    for hid, uid in [("H001", 9), ("H002", 8)]:
        db = get_session(f"hospital_{hid}")
        for r in db.execute(text(
            "SELECT DISTINCT rind.item_name FROM report_info ri"
            " JOIN report_indicator rind ON rind.report_id = ri.id"
            " WHERE ri.user_id = :u"
        ), {"u": uid}):
            names.add(r[0])
        db.close()

    result = asyncio.run(generate(sorted(names)))
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"candidates written: {args.output}")
    print(f"single={len(result.get('single', []))} combos={len(result.get('combos', []))}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行生成候选(MedGo 已跑在 :8004)**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m app.modules.risk.gen_candidates --output app/modules/risk/candidates/llm-candidates-2026-08-14.json
```

Expected: 输出 single/combos 计数,JSON 文件生成

- [ ] **Step 3: 人工审核后回填种子**

把审核通过的条目追加进 `seed.py` 的 `CENTRAL_MAPPINGS` / `CENTRAL_RULES`(保持元组格式),再跑 Task 3 Step 4 的同步命令。

- [ ] **Step 4: Commit**

```bash
git add backend/app/modules/risk/gen_candidates.py
git commit -m "feat(risk): one-shot LLM candidate generation CLI"
```

---

### Task 5: risk.hit 队列 + risk worker

**Files:**
- Modify: `backend/app/core/rabbitmq.py`(QUEUES / RETRY_QUEUES 各加 1 行)
- Create: `backend/app/modules/risk/worker.py`
- Test: `backend/tests/test_risk_worker.py`

- [ ] **Step 1: 注册队列**

`app/core/rabbitmq.py` 的 `QUEUES` dict 内加一行 `"risk.hit": "risk.hit",`;
`RETRY_QUEUES` dict 内加一行 `"risk.hit.retry": "risk.hit",`。

- [ ] **Step 2: 写失败测试(直测 compute_and_store)**

```python
# backend/tests/test_risk_worker.py
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.risk.models import DiseaseRule, DiseaseHit  # noqa: F401
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation  # noqa: F401
from app.modules.risk import worker as risk_worker


def _seed(s):
    s.execute(text("INSERT INTO report_info (id, user_id, name, unit_name, report_date)"
                   " VALUES (1, 10, '张三', '北京医院', '2026-08-14')"))
    s.execute(text("INSERT INTO report_indicator (id, report_id, item_name,"
                   " item_name_standard) VALUES (1, 1, '血肌酸激酶', '肌酸激酶')"))
    s.execute(text("INSERT INTO report_indicator (id, report_id, item_name,"
                   " item_name_standard) VALUES (2, 1, '收缩压', '收缩压')"))
    s.execute(text("INSERT INTO report_indicator (id, report_id, item_name,"
                   " item_name_standard) VALUES (3, 1, '舒张压', '舒张压')"))
    s.execute(text("INSERT INTO report_interpretation (id, report_id, status)"
                   " VALUES (1, 1, 'completed')"))
    s.execute(text("INSERT INTO indicator_judgment (interpretation_id, indicator_id,"
                   " item_name, color_level, source)"
                   " VALUES (1, 1, '血肌酸激酶', 'yellow', 'indicator')"))
    s.execute(text("INSERT INTO indicator_judgment (interpretation_id, indicator_id,"
                   " item_name, color_level, source)"
                   " VALUES (1, 2, '收缩压', 'yellow', 'indicator')"))
    s.execute(text("INSERT INTO indicator_judgment (interpretation_id, indicator_id,"
                   " item_name, color_level, source)"
                   " VALUES (1, 3, '舒张压', 'yellow', 'indicator')"))
    s.execute(text("INSERT INTO disease_mapping (item_name_standard, disease_name,"
                   " disease_category, source, enabled)"
                   " VALUES ('肌酸激酶', '横纹肌溶解', 'CHRONIC', 'CENTRAL', 1)"))
    s.execute(text("INSERT INTO disease_rule (rule_code, disease_name,"
                   " disease_category, member_items, source, enabled)"
                   " VALUES ('C-HT', '高血压', 'CHRONIC',"
                   " '[\\\"收缩压\\\",\\\"舒张压\\\"]', 'CENTRAL', 1)"))
    s.commit()


def test_compute_and_store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    _seed(s)
    n = risk_worker.compute_and_store(s, 1)
    assert n == 2
    rows = s.execute(text(
        "SELECT disease_name, hit_type, hit_items FROM disease_hit ORDER BY disease_name"
    )).all()
    assert [r[0] for r in rows] == ["横纹肌溶解", "高血压"]
    assert rows[1][1] == "combo"
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_risk_worker.py -v
```

Expected: FAIL(ModuleNotFoundError: No module named 'app.modules.risk.worker')

- [ ] **Step 4: 实现 worker(sqlite/MySQL 双兼容:先查后插)**

```python
# backend/app/modules/risk/worker.py
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
         "disease_class": r.disease_class}
        for r in db.execute(text(
            "SELECT id, item_name_standard, disease_name, disease_category,"
            " disease_class FROM disease_mapping WHERE enabled=1"
            " AND disease_category IN ('CHRONIC','MAJOR')"
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


def compute_and_store(db, report_id):
    info = db.execute(text(
        "SELECT user_id, unit_name, report_date FROM report_info WHERE id=:rid"
    ), {"rid": report_id}).first()
    if not info:
        return 0
    judgments = list(db.execute(text(
        "SELECT ij.id, ij.indicator_id, ij.item_name, ij.color_level"
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
            self.indicator_id = row[1]

    mappings, rules = _load_rules(db)
    hits = compute_hits([J(r) for r in judgments], std_names, mappings, rules)
    for h in hits:
        existing = db.execute(text(
            "SELECT id FROM disease_hit WHERE report_id=:rid AND disease_name=:dn"
        ), {"rid": report_id, "dn": h["disease_name"]}).first()
        if existing:
            db.execute(text(
                "UPDATE disease_hit SET hit_type=:ht, hit_items=:items,"
                " mapping_id=:mid, rule_id=:ruid WHERE id=:id"
            ), {"ht": h["hit_type"], "items": json.dumps(h["hit_items"]),
                "mid": h["mapping_id"], "ruid": h["rule_id"], "id": existing.id})
        else:
            db.execute(text(
                "INSERT INTO disease_hit (report_id, user_id, unit_name, report_date,"
                " disease_name, disease_category, disease_class, hit_type,"
                " mapping_id, rule_id, hit_items)"
                " VALUES (:rid, :uid, :un, :rd, :dn, :dc, :dk, :ht, :mid, :ruid, :items)"
            ), {"rid": report_id, "uid": info.user_id, "un": info.unit_name,
                "rd": info.report_date, "dn": h["disease_name"],
                "dc": h["disease_category"], "dk": h["disease_class"],
                "ht": h["hit_type"], "mid": h["mapping_id"],
                "ruid": h["rule_id"], "items": json.dumps(h["hit_items"])})
    db.commit()
    return len(hits)


def handle_risk_task(message: dict):
    routing_key = message.get("_routing_key", "risk.hit")
    payload = message.get("payload", {})
    report_id = payload.get("report_id")
    hospital_id = payload.get("hospital_id")
    if not report_id or not hospital_id:
        return
    db = next(get_hospital_db(hospital_id))
    try:
        n = compute_and_store(db, report_id)
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
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_risk_worker.py -v
```

Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/rabbitmq.py backend/app/modules/risk/worker.py backend/tests/test_risk_worker.py
git commit -m "feat(risk): risk.hit queue + worker computing hits into disease_hit"
```

---

### Task 6: interpretation worker 完成后投递 risk.hit

**Files:**
- Modify: `backend/app/modules/interpretation/worker.py`
- Test: `backend/tests/test_interp_worker_risk.py`

- [ ] **Step 1: 加投递代码**

在 `handle_interpretation_task` 成功路径末尾(`BatchService.increment_progress(db, batch_id, file_id, "interp_ok")` 之后)加:

```python
            # 解读完成 → 异步触发病种规则命中计算(不阻塞本链路)
            try:
                rabbitmq.publish(TaskMessage(
                    task_type="risk",
                    hospital_id=hospital_id,
                    priority="normal",
                    payload={"report_id": report_id},
                ))
            except Exception as e:
                _log.warning("risk publish failed report=%s: %s", report_id, e)
```

顶部 import 改为:`from app.core.rabbitmq import rabbitmq, _NackOnce, TaskMessage`

- [ ] **Step 2: 写测试(解读成功路径会 publish risk 消息)**

```python
# backend/tests/test_interp_worker_risk.py
from unittest.mock import MagicMock, patch


def test_interp_ok_publishes_risk():
    from app.modules.interpretation import worker as w
    mq = MagicMock()
    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
    gen = iter([db])
    with patch("app.modules.interpretation.worker.rabbitmq", mq), \
         patch("app.modules.interpretation.worker.run_interpretation_agent"), \
         patch("app.modules.interpretation.worker.try_generate_comparison_summary"), \
         patch("app.modules.interpretation.worker.BatchService"), \
         patch("app.modules.interpretation.worker._extract_abnormalities_async"), \
         patch("app.modules.interpretation.worker._store_abnormalities"), \
         patch("app.modules.interpretation.worker.refresh_interpretation_counts"), \
         patch("app.modules.interpretation.worker.ReportInfo"), \
         patch("app.modules.interpretation.worker.get_hospital_db",
               lambda hid: gen):
        w.handle_risk_publish_checked = True
        msg = {"_routing_key": "interpretation.normal", "hospital_id": "H001",
               "payload": {"report_id": 5}}
        w.handle_interpretation_task(msg)
    published = [c for c in mq.method_calls if c[0] == "publish"]
    assert len(published) == 1
```

- [ ] **Step 3: 跑测试**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_interp_worker_risk.py -v
```

Expected: 1 passed(若 db mock 细节导致异常路径,按实际 worker 结构补充 patch;核心断言:成功路径 publish 一次)

- [ ] **Step 4: Commit**

```bash
git add backend/app/modules/interpretation/worker.py backend/tests/test_interp_worker_risk.py
git commit -m "feat(risk): interpretation worker publishes risk.hit after completion"
```

---

### Task 7: start.sh 启动 risk worker

**Files:**
- Modify: `start.sh`(pkill 块 + worker 启动块)

- [ ] **Step 1: pkill 块加一行**

```bash
  pkill -f "app.modules.risk.worker" 2>/dev/null || true
```

- [ ] **Step 2: 启动块加 risk worker(沿用 -u 无缓冲,参考 interpretation worker)**

```bash
if pgrep -f "app.modules.risk.worker" >/dev/null 2>&1; then
  log "风险规则引擎 Worker 已在运行"
else
  nohup $VENV/python -u -c "from app.modules.risk.worker import start_worker; start_worker()" > /data/logs/worker-risk.stdout.log 2>&1 &
  echo $! > /tmp/start-sh-worker-risk.pid
  log "  风险规则引擎 Worker 已启动 (log: /data/logs/worker-risk.stdout.log)"
fi
```

- [ ] **Step 3: 手动启动验证(不重启全栈)**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend
setsid env BULK_WINDOW_START=0 BULK_WINDOW_END=24 nohup .venv/bin/python -u -c "from app.modules.risk.worker import start_worker; start_worker()" > /tmp/logs/worker-risk.log 2>&1 &
sleep 3 && curl -s -u root:root http://localhost:15672/api/queues/hospital_dev/risk.hit | python3 -m json.tool | grep consumers
```

Expected: `"consumers": 1`

- [ ] **Step 4: Commit**

```bash
git add start.sh
git commit -m "chore(start.sh): launch risk engine worker"
```

---

### Task 8: 历史回填脚本

**Files:**
- Create: `backend/scripts/backfill_disease_hits.py`

- [ ] **Step 1: 写脚本**

```python
# backend/scripts/backfill_disease_hits.py
"""一次性回填:对所有已 completed 解读的报告重算病种命中。

用法: cd backend && .venv/bin/python scripts/backfill_disease_hits.py [--hospital H001] [--limit 0]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.core.database import get_session
from app.modules.risk.worker import compute_and_store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hospital", default=None)
    ap.add_argument("--limit", type=int, default=0, help="0=全部")
    args = ap.parse_args()

    hospitals = [args.hospital] if args.hospital else ["H001", "H002", "H003", "H004"]
    for hid in hospitals:
        db = get_session(f"hospital_{hid}")
        ids = [r[0] for r in db.execute(text(
            "SELECT DISTINCT ri.report_id FROM report_interpretation ri"
            " WHERE ri.status='completed'"
        ))]
        if args.limit:
            ids = ids[:args.limit]
        total = 0
        for rid in ids:
            try:
                total += compute_and_store(db, rid)
            except Exception as e:
                print(f"{hid} report={rid} fail: {e}")
        db.close()
        print(f"{hid}: {len(ids)} reports, {total} hits")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑回填(H001 为演示库,数据最多)**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python scripts/backfill_disease_hits.py --hospital H001
```

Expected: 打印 `H001: N reports, M hits` 且 M > 0

- [ ] **Step 3: 抽样验证**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -c "
from app.core.database import get_session
from sqlalchemy import text
db = get_session('hospital_H001')
for r in db.execute(text('SELECT report_id, user_id, unit_name, disease_name, disease_category, hit_type, hit_items FROM disease_hit LIMIT 10')):
    print(r)
db.close()"
```

Expected: 行有真实 user_id/unit_name/disease_name

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/backfill_disease_hits.py
git commit -m "feat(risk): backfill script for historical reports"
```

---

### Task 9: 统计端点 disease 模式改查 disease_hit

**Files:**
- Modify: `backend/app/modules/statistics/disease_service.py`
- Test: `backend/tests/test_risk_stats.py`

- [ ] **Step 1: 修改三个函数(disease 模式数据源切换)**

`disease_service.py` 关键改动:

```python
# 顶部:新增 disease_hit 维度过桥常量(供 disease 模式使用)
_HIT_DIM_EXPR = {
    "gender": "CASE WHEN ri.gender IN ('男', '女') THEN ri.gender ELSE '未知' END",
    "age_group": _AGE_GROUP_CASE,
    "unit": "COALESCE(NULLIF(dh.unit_name, ''), '未知')",
}

def _dim_expr_hit(dimension: str) -> str:
    return _HIT_DIM_EXPR.get(dimension, _HIT_DIM_EXPR["unit"])
```

`indicator_cross` 中,`stat_mode == "disease"` 分支:

```python
    if stat_mode == "disease":
        dim_hit = _dim_expr_hit(dimension)
        sample_sql = f"""
            SELECT {dim} AS label, COUNT(DISTINCT ri.user_id, ri.report_date) AS sample_size
            FROM report_info ri
            WHERE ri.report_date BETWEEN :start AND :end
            {_DEMO_FILTER}
            GROUP BY label
        """
        count_sql = f"""
            SELECT {dim_hit} AS label, dh.disease_name AS item_name,
                   COUNT(DISTINCT dh.user_id, dh.report_date) AS cnt
            FROM disease_hit dh
            JOIN report_info ri ON ri.id = dh.report_id
            WHERE dh.report_date BETWEEN :start AND :end
              {_DEMO_FILTER}
            GROUP BY label, item_name
        """
```

`disease_trend` 中,`stat_mode == "disease"` 分支:

```python
    if stat_mode == "disease":
        count_sql = f"""
            SELECT YEAR(dh.report_date) AS y, dh.disease_name AS item_name,
                   COUNT(DISTINCT dh.user_id, dh.report_date) AS cnt
            FROM disease_hit dh
            JOIN report_info ri ON ri.id = dh.report_id
            WHERE YEAR(dh.report_date) BETWEEN :y0 AND :y1
              {_DEMO_FILTER}
              {filters}
            GROUP BY y, item_name
        """
```

`unit_disease_spectrum` 中,`stat_mode == "disease"` 分支:

```python
    if stat_mode == "disease":
        top_sql = f"""
            SELECT dh.disease_name AS item_name,
                   COUNT(DISTINCT dh.user_id, dh.report_date) AS cnt
            FROM disease_hit dh
            JOIN report_info ri ON ri.id = dh.report_id
            WHERE dh.report_date BETWEEN :start AND :end
              {_DEMO_FILTER}
              {unit_filter}
            GROUP BY item_name ORDER BY cnt DESC LIMIT :topn
        """
        class_sql = f"""
            SELECT COALESCE(NULLIF(dh.disease_class, ''), '未分类') AS class_name,
                   COUNT(DISTINCT dh.user_id, dh.report_date) AS cnt
            FROM disease_hit dh
            JOIN report_info ri ON ri.id = dh.report_id
            WHERE dh.report_date BETWEEN :start AND :end
              {_DEMO_FILTER}
              {unit_filter}
            GROUP BY class_name ORDER BY cnt DESC
        """
```

注意:
- disease 模式不再用 `_DISEASE_JOIN`、`_SRC_INDICATOR_FILTER`、`_EXCLUDE_INDICATOR_ITEMS_FILTER` 与 `indicator_judgment` 源(这些常量仅 indicator 模式或旧路径使用,勿删 indicator 分支)
- `filters`(category/diseases 过滤)在 disease 模式下仍对 `dh.disease_name` 生效,`unit_filter` 对 `dh.unit_name` 生效
- 样本量(sample_sql)口径不变(仍按 report_info 日期区间)

- [ ] **Step 2: 写测试**

```python
# backend/tests/test_risk_stats.py
from app.modules.statistics import disease_service as ds


def test_dim_expr_hit_uses_dh_unit():
    assert "dh.unit_name" in ds._dim_expr_hit("unit")


def test_dim_expr_hit_age_group():
    assert "ri.age" in ds._dim_expr_hit("age_group")
```

- [ ] **Step 3: 跑测试 + 手动接口验证**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_risk_stats.py -v
KEY=$(grep STAT_SERVICE_API_KEY .env | cut -d= -f2)
curl -s "http://localhost:8005/api/statistics/disease/unit-disease-spectrum?start_date=2026-01-01&end_date=2026-12-31&stat_mode=disease" -H "X-Api-Key: $KEY" | head -c 500
```

Expected: 2 passed;curl 返回 top_diseases 含已回填病种(需先跑 Task 8 回填)

- [ ] **Step 4: Commit**

```bash
git add backend/app/modules/statistics/disease_service.py backend/tests/test_risk_stats.py
git commit -m "feat(risk): disease-mode statistics read from disease_hit table"
```

---

### Task 10: 预留映射 CRUD 接口(服务间鉴权)

**Files:**
- Create: `backend/app/modules/risk/router.py`
- Modify: `backend/app/main.py`(include_router)
- Test: `backend/tests/test_risk_router.py`

- [ ] **Step 1: 写接口**

```python
# backend/app/modules/risk/router.py
"""映射/组合规则管理 CRUD(预留):供 sz-mana Java 后台调用(服务间鉴权)。

本期范围: 列表 + 启用/停用 + 新增本院私有规则。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.service_auth import require_service_client
from app.modules.risk.models import DiseaseRule

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
         " disease_class, source, enabled, sort_code FROM disease_mapping")
    params = {}
    if category:
        q += " WHERE disease_category = :c"
        params["c"] = category
    rows = db.execute(text(q), params).mappings().all()
    return {"items": [dict(r) for r in rows]}


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
    member_items: list[str]


@router.post("/rules")
def create_rule(req: RuleCreate, db: Session = Depends(_get_db)):
    existing = db.query(DiseaseRule).filter(
        DiseaseRule.rule_code == req.rule_code).first()
    if existing:
        return {"ok": False, "error": "rule_code exists"}
    db.add(DiseaseRule(rule_code=req.rule_code, disease_name=req.disease_name,
                       disease_category=req.disease_category,
                       disease_class=req.disease_class,
                       member_items=req.member_items, source="LOCAL"))
    db.commit()
    return {"ok": True}


@router.get("/rules")
def list_rules(db: Session = Depends(_get_db)):
    rows = db.query(DiseaseRule).all()
    return {"items": [{
        "id": r.id, "rule_code": r.rule_code, "disease_name": r.disease_name,
        "disease_category": r.disease_category, "disease_class": r.disease_class,
        "member_items": r.member_items, "source": r.source, "enabled": r.enabled,
    } for r in rows]}
```

- [ ] **Step 2: main.py 挂载**

在 `app/main.py` 现有 include_router 区域追加:

```python
from app.modules.risk.router import router as risk_router
app.include_router(risk_router, prefix="/api", tags=["risk"])
```

- [ ] **Step 3: 写测试**

```python
# backend/tests/test_risk_router.py
def test_routes_exist():
    from app.modules.risk.router import router
    paths = {r.path for r in router.routes}
    assert "/risk/mappings" in paths
    assert "/risk/rules" in paths
    assert "/risk/mappings/{mapping_id}/enabled" in paths
```

- [ ] **Step 4: 跑测试**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/test_risk_router.py -v
```

Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/risk/router.py backend/app/main.py backend/tests/test_risk_router.py
git commit -m "feat(risk): reserved mapping/rule CRUD endpoints (service auth)"
```

---

### Task 11: 全量回归 + 文档更新

**Files:**
- Modify: `AGENTS.md`(新增表 + risk worker 说明)

- [ ] **Step 1: 全量测试**

```bash
cd /home/wjyy2/hospitalKnowledgeBase/backend && .venv/bin/python -m pytest tests/ -q
```

Expected: 全绿(既有 + 新增)

- [ ] **Step 2: 端到端验证(H003/H004 已有报告)**

```bash
curl -s -u root:root http://localhost:15672/api/queues/hospital_dev/risk.hit | python3 -m json.tool | grep -E 'consumers|messages'
cd /home/wjyy2/hospitalKnowledgeBase/backend
.venv/bin/python scripts/backfill_disease_hits.py --hospital H003
.venv/bin/python scripts/backfill_disease_hits.py --hospital H004
.venv/bin/python -c "
from app.core.database import get_session
from sqlalchemy import text
for hid in ['H003','H004']:
    db = get_session(f'hospital_{hid}')
    for r in db.execute(text('SELECT report_id, user_id, disease_name, disease_category, hit_type, hit_items FROM disease_hit')):
        print(hid, r)
    db.close()"
```

Expected: H003/H004 各有 ≥1 条命中(如 高血压 combo、血脂异常 single)

- [ ] **Step 3: 更新 AGENTS.md**

在"批量上传新增表(易遗漏)"表后追加:

```markdown
| 病种规则新增表 | 用途 |
|------|------|
| `disease_rule` | 异常指标→病种 严格AND组合规则(rule_code 唯一,source=CENTRAL/LOCAL) |
| `disease_hit` | 报告粒度病种命中固化(uk: report_id+disease_name;冗余 user_id/unit_name/report_date) |

`disease_mapping` 新增 `source` 列(旧库需 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'LOCAL'`,`start.sh` 已带)。

risk 引擎 worker 启动:`nohup $VENV/python -u -c "from app.modules.risk.worker import start_worker; start_worker()" > /data/logs/worker-risk.stdout.log 2>&1 &`;无 bulk 窗口限制。
```

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): disease_rule/disease_hit tables + risk worker notes"
```

---

## Self-Review

**1. Spec coverage(对照 12 项决策):**

| 决策 | 计划落点 |
|------|---------|
| 页面归属 Java 系统(纯后端) | Task 9 统计端点 + Task 10 CRUD(服务间鉴权) |
| CHRONIC+MAJOR 补全建实 | Task 3 seed + Task 4 LLM 候选 |
| LLM 生成候选+人工审核 | Task 4 脚本产出 JSON → 审核后并入 seed.py |
| 严格 AND 组合 | Task 2 engine `all(...)` |
| 只依赖 red/yellow 判定 | Task 2 `_abnormal_name_set`(不读数值) |
| 方案 A:落库时固化命中 | Task 5 compute_and_store → disease_hit |
| 独立新表、报告粒度、冗余全维度 | Task 1 DiseaseHit(user_id/unit_name/report_date) |
| 中央标准表+各库同步+本院私有 | Task 3 source 列 + sync_central 保留 LOCAL |
| 独立队列不占用户链路 | Task 5/6:interp 完成后 publish,risk worker 异步消费 |
| MAJOR 标准疾病名+"疑似"口径 | Task 3 seed 命名"恶性肿瘤(疑似)" |
| 静态文件+预留 CRUD | Task 3 seed.py + Task 10 router |
| 组合规则仅必要时 | Task 4 prompt 约束 |

**2. Placeholder scan:** 无 TBD/TODO;每步含完整代码与命令。

**3. Type consistency:** `compute_hits(judgments, indicator_std_names, mappings, rules)` 签名在 Task 2 定义,Task 5 worker 以同名调用;`hit_items` 字段名 Task 1/2/5/8/9 一致;`disease_hit` 列名 Task 1 DDL 与 Task 5 插入一致。

**风险提示:**
- Task 5/8 对 sqlite 测试环境无 MySQL `ON DUPLICATE KEY` 依赖(已用先查后插);
- Task 9 修改统计 SQL 时,indicator 模式分支保持不动,只切 disease 分支;
- Task 4 的 LLM 输出需要人工审核后才能进 seed,避免未经审核的映射污染统计。
