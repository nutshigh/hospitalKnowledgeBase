# 指标标准名子串吞噬修复 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉 term_normalizer 子串匹配把血常规/尿检子项误并入父项标准名的缺陷,让指标走势只留主项、给聚合加防线,并回填当前管线库存量标准名。

**Architecture:** 三层落地 —— (1) `app/core/term_normalizer.py` 改「整名精确匹配 + 词表打标(primary/child)」,结构性杜绝子串吞噬;(2) `user_profile/service.py` 走势只保留主项,并给 `get_overview` / `_series` 加「同报告同 key 多 item_name 拆系列」防线;(3) 新增存量回填脚本对 `hospital_1/H001/H002` 重算 `item_name_standard`。

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy / MySQL(宿主 docker mysql:8) / pytest(SQLite in-memory)。

## Global Constraints

- 依赖 pin 不变:不得改动 `backend/pyproject.toml` / uv.lock / vLLM venv / 驱动相关(与 cu12 锁定无关)。
- 代码风格:不加无关注释;中文注释沿用现状风格;保持 `normalize_item_name` 的 `(standard, code)` 索引 0 语义(现有测试 `normalize_item_name("血糖")[0]` 依赖它)。
- canonical 字符串:`_STANDARD_MAP` 现 17 个主项标准名**逐字不变**(如 `血小板计数（PLT）`、`血红蛋白（Hb）`),只改别名匹配方式并新增子项词条。
- 子项 canonical 一律 `primary=False`;走势(`get_overview` 的 `indicator_trends`)只含 `primary` 主项;`/profile/change-overview`(`_series` / `key_indicators` / AI 总结)**保留全量含子项**。
- 存量回填只动 `hospital_1` / `hospital_H001` / `hospital_H002`;**不动** `hospital_H003` / `hospital_H004`。
- 所有测试从 `backend` 目录运行:`cd backend && .venv/bin/python -m pytest <路径> -q`。

---

### Task 1: term_normalizer 整名匹配 + 词表打标

**Files:**
- Modify: `backend/app/core/term_normalizer.py`(整文件重写)
- Test: `backend/tests/core/test_term_normalizer.py`(顶部 import 与新增用例)

**Interfaces:**
- Consumes: 无(纯函数模块)。
- Produces:
  - `normalize_item_name(raw_name: str) -> tuple[str, None]` —— 语义保留:命中别名返回 canonical;未命中返回 `raw_name.strip()` 透传。**不再做子串包含匹配。**
  - `normalize_indicators(indicators) -> list[dict]` —— 行为不变(设 standard + 去重)。
  - `is_child_item(item_name: str) -> bool` —— raw 名解析为 `primary=False` 子项 → True(供 Task 2 走势过滤)。
  - `resolve_canonical(raw_name) -> CanonTerm | None`(内部/回填脚本可用)。

- [ ] **Step 1: 写失败测试**(先证明旧实现吞子项)

在 `backend/tests/core/test_term_normalizer.py` 顶部把 import 改为:

```python
from app.core.term_normalizer import (
    normalize_indicators,
    normalize_item_name,
    is_child_item,
)
```

并在文件末尾追加:

```python
def test_no_substring_swallow_child_into_parent():
    """血常规子项不再被子串吞成父项,各自映射到子项 canonical。"""
    assert normalize_item_name("血小板比积")[0] == "血小板比积（PCT）"
    assert normalize_item_name("血小板平均体积")[0] == "血小板平均体积（MPV）"
    assert normalize_item_name("血小板分布宽度")[0] == "血小板分布宽度（PDW）"
    assert normalize_item_name("大血小板比率")[0] == "大血小板比率（P-LCR）"
    assert normalize_item_name("血小板计数")[0] == "血小板计数（PLT）"
    assert normalize_item_name("平均红细胞体积")[0] == "平均红细胞体积（MCV）"
    assert normalize_item_name("平均血红蛋白浓度")[0] == "平均红细胞血红蛋白浓度（MCHC）"
    assert normalize_item_name("小而密低密度脂蛋白胆固醇")[0] == "小而密低密度脂蛋白胆固醇（sdLDL）"


def test_no_substring_swallow_urine_or_pH():
    """含父名词干的尿检/酸碱度项不得并入父项,保持原名透传。"""
    assert normalize_item_name("尿白细胞酯酶")[0] == "尿白细胞酯酶"
    assert normalize_item_name("尿白细胞（镜检）")[0] == "尿白细胞（镜检）"
    assert normalize_item_name("尿酸碱度")[0] == "尿酸碱度"


def test_trailing_english_code_paren_stripped_for_lookup():
    """尾缀英文码括号可剥(base 命中 canonical);中文括号限定语不剥。"""
    assert normalize_item_name("血红蛋白(HGB)")[0] == "血红蛋白（Hb）"
    assert normalize_item_name("血小板计数（PLT）")[0] == "血小板计数（PLT）"
    assert normalize_item_name("尿红细胞（镜检）")[0] == "尿红细胞（镜检）"


def test_is_child_item_flags():
    assert is_child_item("血小板比积") is True
    assert is_child_item("红细胞压积") is True
    assert is_child_item("小而密低密度脂蛋白胆固醇") is True
    assert is_child_item("血小板计数") is False
    assert is_child_item("尿酸碱度") is False
    assert is_child_item("") is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_term_normalizer.py -q`
Expected: FAIL —— 如 `assert normalize_item_name("血小板比积")[0] == "血小板比积（PCT）"` 处实际得到 `"血小板计数（PLT）"`(旧子串吞噬)。

- [ ] **Step 3: 整文件重写 `backend/app/core/term_normalizer.py`**

```python
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanonTerm:
    """词条。standard 为标准名;primary=False 表示子项/衍生物(指标走势隐藏)。"""
    standard: str
    primary: bool = True


# ---- 匹配辅助 ------------------------------------------------------------

# 清洗:去普通/全角空格
_SPACE = re.compile(r"[\s\u3000]+")

# 尾缀英文码括号 (…) /（…）。内容须为英文/数字/常用符号;中文限定语括号(镜检/尿/粪)不剥。
_CODE_PAREN = re.compile(
    r"[（(]\s*[A-Za-z0-9][A-Za-z0-9%.\-+#/:]*\s*[)）]\s*$"
)


def _clean(name: str) -> str:
    return _SPACE.sub("", name or "")


def _base(name: str) -> str:
    cleaned = _clean(name)
    if _CODE_PAREN.search(cleaned):
        return _CODE_PAREN.sub("", cleaned)
    return cleaned


# ---- 别名表:整名(清洗后)→ 词条 ------------------------------------------
# 匹配顺序:先整名(含括号)精确命中;未命中再剥尾缀英文码括号后的 base 精确命中。
# 未命中一律原名透传 —— 结构性杜绝「短别名吞长词」。

_ALIASES: Dict[str, CanonTerm] = {
    # ==== 主项(与历史 _STANDARD_MAP 的 canonical 字符串逐字一致) ====
    # 空腹血糖
    "血糖": CanonTerm("空腹血糖（GLU）"),
    "葡萄糖": CanonTerm("空腹血糖（GLU）"),
    "空腹血糖": CanonTerm("空腹血糖（GLU）"),
    # 糖化血红蛋白(含“全血糖化血红蛋白测定”这类含血糖词干但实为糖化的项)
    "糖化血红蛋白": CanonTerm("糖化血红蛋白（HbA1c）"),
    "全血糖化血红蛋白测定": CanonTerm("糖化血红蛋白（HbA1c）"),
    # 血脂
    "总胆固醇": CanonTerm("总胆固醇（TC）"),
    "甘油三酯": CanonTerm("甘油三酯（TG）"),
    "高密度脂蛋白": CanonTerm("高密度脂蛋白胆固醇（HDL-C）"),
    "高密度脂蛋白胆固醇": CanonTerm("高密度脂蛋白胆固醇（HDL-C）"),
    "低密度脂蛋白": CanonTerm("低密度脂蛋白胆固醇（LDL-C）"),
    "低密度脂蛋白胆固醇": CanonTerm("低密度脂蛋白胆固醇（LDL-C）"),
    # 肝功
    "谷丙转氨酶": CanonTerm("丙氨酸氨基转移酶（ALT）"),
    "谷草转氨酶": CanonTerm("天门冬氨酸氨基转移酶（AST）"),
    # 肾功
    "尿酸": CanonTerm("尿酸（UA）"),
    "肌酐": CanonTerm("肌酐（Cr）"),
    "尿素氮": CanonTerm("尿素氮（BUN）"),
    # 血常规主项
    "白细胞": CanonTerm("白细胞计数（WBC）"),
    "白细胞计数": CanonTerm("白细胞计数（WBC）"),
    "红细胞": CanonTerm("红细胞计数（RBC）"),
    "红细胞计数": CanonTerm("红细胞计数（RBC）"),
    "血红蛋白": CanonTerm("血红蛋白（Hb）"),
    "血小板": CanonTerm("血小板计数（PLT）"),
    "血小板计数": CanonTerm("血小板计数（PLT）"),

    # ==== 血常规子项(primary=False,走势隐藏) ====
    # 血小板系
    "血小板比积": CanonTerm("血小板比积（PCT）", primary=False),
    "血小板比容": CanonTerm("血小板比积（PCT）", primary=False),
    "血小板压积": CanonTerm("血小板比积（PCT）", primary=False),
    "血小板平均体积": CanonTerm("血小板平均体积（MPV）", primary=False),
    "平均血小板体积": CanonTerm("血小板平均体积（MPV）", primary=False),
    "血小板平均容积": CanonTerm("血小板平均体积（MPV）", primary=False),
    "血小板分布宽度": CanonTerm("血小板分布宽度（PDW）", primary=False),
    "血小板体积分布宽度": CanonTerm("血小板分布宽度（PDW）", primary=False),
    "大血小板比率": CanonTerm("大血小板比率（P-LCR）", primary=False),
    "大血小板数": CanonTerm("大血小板比率（P-LCR）", primary=False),
    # 红细胞系
    "红细胞压积": CanonTerm("红细胞压积（HCT）", primary=False),
    "红细胞比容": CanonTerm("红细胞压积（HCT）", primary=False),
    "红细胞比积": CanonTerm("红细胞压积（HCT）", primary=False),
    "平均红细胞体积": CanonTerm("平均红细胞体积（MCV）", primary=False),
    "红细胞平均体积": CanonTerm("平均红细胞体积（MCV）", primary=False),
    "平均红细胞容积": CanonTerm("平均红细胞体积（MCV）", primary=False),
    "平均红细胞血红蛋白量": CanonTerm("平均红细胞血红蛋白量（MCH）", primary=False),
    "平均红细胞血红蛋白含量": CanonTerm("平均红细胞血红蛋白量（MCH）", primary=False),
    "平均血红蛋白量": CanonTerm("平均红细胞血红蛋白量（MCH）", primary=False),
    "平均血红蛋白含量": CanonTerm("平均红细胞血红蛋白量（MCH）", primary=False),
    "平均红细胞血红蛋白浓度": CanonTerm("平均红细胞血红蛋白浓度（MCHC）", primary=False),
    "平均血红蛋白浓度": CanonTerm("平均红细胞血红蛋白浓度（MCHC）", primary=False),
    # RDW:CV 与 SD 各自成子项(带码整名先命中)
    "红细胞分布宽度（CV）": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度(CV)": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度-变异系数": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度变异系数": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度（SD）": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    "红细胞分布宽度(SD)": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    "红细胞分布宽度-标准差": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    "红细胞分布宽度标准差": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    # 无码区分不定的 RDW 变体,归为通用 RDW 子项
    "红细胞体积分布宽度": CanonTerm("红细胞分布宽度（RDW）", primary=False),
    "红细胞分布宽度": CanonTerm("红细胞分布宽度（RDW）", primary=False),
    # NRBC
    "有核红细胞百分比": CanonTerm("有核红细胞计数（NRBC）", primary=False),
    "有核红细胞数": CanonTerm("有核红细胞计数（NRBC）", primary=False),
    "有核红细胞计数": CanonTerm("有核红细胞计数（NRBC）", primary=False),
    # 血脂子项
    "小而密低密度脂蛋白胆固醇": CanonTerm("小而密低密度脂蛋白胆固醇（sdLDL）", primary=False),
}


def _resolve(raw_name: str) -> Optional[CanonTerm]:
    cleaned = _clean(raw_name)
    if not cleaned:
        return None
    term = _ALIASES.get(cleaned)
    if term is not None:
        return term
    base = _base(cleaned)
    if base != cleaned:
        return _ALIASES.get(base)
    return None


def resolve_canonical(raw_name: str) -> Optional[CanonTerm]:
    """raw 整名 → CanonTerm;未命中(含空串/纯括号)返回 None。"""
    return _resolve(raw_name)


def is_child_item(item_name: str) -> bool:
    """raw 名解析为 primary=False 的子项 → True(指标走势隐藏子项)。"""
    if not item_name:
        return False
    term = _resolve(item_name)
    return bool(term and not term.primary)


def normalize_item_name(raw_name: str) -> tuple:
    """名称标准化。命中别名 → canonical(索引0);未命中 → raw_name.strip() 透传。"""
    raw_name = (raw_name or "").strip()
    if not raw_name:
        return "", None
    term = _resolve(raw_name)
    if term:
        return term.standard, None
    return raw_name, None


def normalize_indicators(indicators: list[dict]) -> list[dict]:
    """名称标准化 + 去重。

    体检 PDF 通常在多个章节(主检报告 / 医学科普 / 分项报告)逐一列出同一指标的同一
    数值;LLM 抽取时按章节各返回一条,DB 入库后会出现同名同值的多行。run_rules →
    filter_abnormal 会忠实于 DB 行数,导致 agent_search_knowledge 收到重复指标名、
    发重复 search_knowledge 调用、judge 也对重复指标重复审核。在此按
    (item_name_standard 或 item_name, result) 去重,保留首次出现,顺序不变。
    """
    for ind in indicators:
        name, code = normalize_item_name(ind.get("item_name", ""))
        ind["item_name_standard"] = name
        ind["item_code"] = code

    seen: set = set()
    deduped: list[dict] = []
    for ind in indicators:
        key = (
            ind.get("item_name_standard") or ind.get("item_name", ""),
            str(ind.get("result", "") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ind)

    if len(deduped) != len(indicators):
        logger.info(
            "normalize_indicators deduped %d -> %d (dropped %d duplicates)",
            len(indicators), len(deduped), len(indicators) - len(deduped),
        )
    return deduped
```

注意:旧 `normalize_indicators` 里的 `normalize_item_name(ind.get("item_name", ""))` 返回 `(name, code)`;此实现 `code` 恒为 None,与现状一致。`resolve_canonical` 为供 Task 4 回填脚本引用的公开接口。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_term_normalizer.py -q`
Expected: PASS(全量,含新增 4 条与既有 8 条)。

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/core/term_normalizer.py tests/core/test_term_normalizer.py
git commit -m "fix(term_normalizer): 整名匹配替代子串吞噬,词表打标 primary/child(防血常规子项并入父项)"
```

---

### Task 2: 指标走势只留主项(`get_overview` 过滤子项)

**Files:**
- Modify: `backend/app/modules/user_profile/service.py`(顶部 import + `get_overview` 循环)
- Test: `backend/tests/user_profile/test_service.py`(末尾追加)

**Interfaces:**
- Consumes: `app.core.term_normalizer.is_child_item(item_name) -> bool`(Task 1)。
- Produces: `get_overview` 的 `indicator_trends` 不再含血常规等子项;主项系列每报告 ≤1 点(干净数据下)。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/user_profile/test_service.py` 末尾追加:

```python
def test_get_overview_trends_only_primary_items(db):
    """旧库形态:每份报告父(血小板计数)+4 个子项全挂父标准名下。
    走势应剔除子项,父系列点 = 报告份数(每报告 1 点),不出现子项系列。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment

    parent = "血小板计数"
    children = ["血小板比积", "血小板平均体积", "血小板分布宽度", "大血小板比率"]
    for rid, dt, pv in [(1, date(2024, 6, 1), "300"), (2, date(2025, 6, 1), "319"),
                        (3, date(2026, 6, 1), "210")]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
        db.add(ReportIndicator(id=rid * 100 + 1, report_id=rid, item_name=parent,
                               item_name_standard="血小板计数（PLT）",
                               result_value=pv, unit="x10^9/L"))
        for i, c in enumerate(children):
            db.add(ReportIndicator(id=rid * 100 + 2 + i, report_id=rid, item_name=c,
                                   item_name_standard="血小板计数（PLT）",
                                   result_value=str(int(pv) - 1 - i), unit="%"))
    db.commit()
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=301, item_name=parent,
                             color_level="yellow"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    names = [t["item_name_standard"] for t in result["indicator_trends"]]
    assert names == ["血小板计数（PLT）"]
    plt = result["indicator_trends"][0]
    assert len(plt["points"]) == 3  # 每份报告 1 点,不再 5 点/份
    assert len({p["report_id"] for p in plt["points"]}) == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py::test_get_overview_trends_only_primary_items -q`
Expected: FAIL —— `len(plt["points"]) == 3` 实际为 15(5 行/报告 × 3 报告并线)。

- [ ] **Step 3: 实现子项过滤**

编辑 `backend/app/modules/user_profile/service.py`:

(a) 顶部 import(在 `from app.modules.report.models import ...` 附近的 import 区内新增):

```python
from app.core.term_normalizer import is_child_item
```

(b) `get_overview` 循环(现约 82-88 行)内,数值校验通过后、取 key 前加子项短路:

```python
        try:
            float(str(ind.result_value).strip())
        except (TypeError, ValueError):
            continue
        if is_child_item(ind.item_name or ""):
            continue
        key = ind.item_name_standard or ind.item_name
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py -q`
Expected: PASS(新增用例 + 既有全部,既有用例无血常规子项,不受影响)。

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/modules/user_profile/service.py tests/user_profile/test_service.py
git commit -m "feat(profile): 指标走势只留主项,子项(血常规衍生物等)不再成走势系列"
```

---

### Task 3: 聚合防线(同报告多 item_name 拆系列)

**Files:**
- Modify: `backend/app/modules/user_profile/service.py`
  - `get_overview`(汇聚点携带 `item_name`/`unit`、构建后跑守卫)
  - `_series`(同上)
  - 新增模块级守卫函数 `_split_item_name_collisions`
- Test: `backend/tests/user_profile/test_change_overview.py`(末尾追加)

**Interfaces:**
- Consumes: `_series(db, window) -> list[dict]`(既有);`trend_direction(...)`。
- Produces:
  - `_split_item_name_collisions(items: list[dict]) -> list[dict]`:任一系列若同一 `report_id` 出现多个不同 `item_name`,按 `(item_name, unit)` 拆成独立系列并 `logger.warning`;同名重复行不拆。
  - `get_overview` 与 `_series` 的点 dict 新增 `item_name`、`unit` 两键(仅内部防线消费;前端忽略额外键)。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/user_profile/test_change_overview.py` 末尾追加:

```python
def test_series_splits_same_report_different_item_names(db):
    """旧库吞噬形态:两份报告里“血小板计数”与“血小板比积”同挂标准名。
    _series 应把不同 item_name 拆成各自系列,杜绝异量纲并线。"""
    from app.modules.user_profile.service import _series

    for rid in (1, 2):
        db.add(ReportInfo(id=rid, user_id="u1", name="甲", report_date=date(2025, rid, 1)))
        db.add(ReportIndicator(id=rid * 100 + 1, report_id=rid, item_name="血小板计数",
                               item_name_standard="血小板计数（PLT）",
                               result_value="300", unit="x10^9/L"))
        db.add(ReportIndicator(id=rid * 100 + 2, report_id=rid, item_name="血小板比积",
                               item_name_standard="血小板计数（PLT）",
                               result_value="0.29", unit="%"))
        db.add(ReportInterpretation(id=rid, report_id=rid, overall_level="green",
                                    status="completed", red_count=0, yellow_count=0, green_count=0))
    db.commit()
    rows = db.query(ReportInfo, ReportInterpretation).join(
        ReportInterpretation, ReportInterpretation.report_id == ReportInfo.id).all()
    window = list(rows)

    series = _series(db, window)
    by_name = {s["item_name"]: s for s in series}
    assert set(by_name) == {"血小板计数", "血小板比积"}
    for s in series:
        per_report = {}
        for p in s["points"]:
            per_report.setdefault(p["report_id"], set()).add(p.get("item_name"))
        assert all(len(n) == 1 for n in per_report.values())
    assert [p["value"] for p in by_name["血小板计数"]["points"]] == ["300", "300"]
    assert [p["value"] for p in by_name["血小板比积"]["points"]] == ["0.29", "0.29"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_change_overview.py::test_series_splits_same_report_different_item_names -q`
Expected: FAIL —— `set(by_name) == {"血小板计数", "血小板比积"}` 实际只有 `{"血小板计数（PLT）"}`(未拆)。

- [ ] **Step 3: 实现守卫并接线**

编辑 `backend/app/modules/user_profile/service.py`:

(a) 新增模块级守卫函数(放在 `_auto_select_baseline` 函数定义之后、`get_overview` 之前):

```python
def _split_item_name_collisions(items: list[dict]) -> list[dict]:
    """防线:同一系列内若同一份报告出现多个不同 item_name(标准名吞噬造成的脏数据,
    如血常规子项被并入父项),按 (item_name, unit) 拆成独立系列,避免异量纲数值画成一条线。

    同名重复行(如同一报告同一指标多次测量)不拆 —— 交给 distinct-report 门/展示语义处理。
    """
    out: list[dict] = []
    for item in items:
        names_by_report: dict = {}
        for p in item["points"]:
            names_by_report.setdefault(p["report_id"], set()).add(p.get("item_name"))
        if not any(len(names) > 1 for names in names_by_report.values()):
            out.append(item)
            continue
        logger.warning(
            "indicator series %r mixes distinct item_name within one report; "
            "splitting by item_name/unit", item.get("item_name"),
        )
        groups: dict = {}
        for p in item["points"]:
            gkey = (p.get("item_name"), p.get("unit"))
            g = groups.setdefault(gkey, {
                "item_name_standard": gkey[0],
                "item_name": gkey[0],
                "unit": gkey[1],
                "points": [],
            })
            g["points"].append(p)
        for g in groups.values():
            g["points"].sort(key=lambda p: (p["report_date"] is not None, p["report_date"] or ""))
            out.append(g)
    return out
```

(b) `get_overview` 点 dict 补 `item_name`/`unit`,并在构建后跑守卫。现有点 dict(约 98-103 行)改为:

```python
        by_key[key]["points"].append({
            "report_id": ind.report_id,
            "report_date": report_map[ind.report_id].report_date.isoformat() if report_map[ind.report_id].report_date else None,
            "value": float(str(ind.result_value).strip()),
            "color": judgment.color_level if judgment else None,
            "item_name": ind.item_name,
            "unit": ind.unit,
        })
```

紧跟其后的「排序 + 算 trend_direction/latest_deviation + 过滤红黄」块(现约 105-119 行)整体改为:

```python
    trend_items = _split_item_name_collisions(list(by_key.values()))
    for v in trend_items:
        v["points"].sort(key=lambda p: (p["report_date"] is not None, p["report_date"] or ""))
        v["trend_direction"] = trend_direction(v["points"])
        v["latest_deviation"] = v["points"][-1].get("color") if v["points"] else None

    def _abnormal_sev(points: list[dict]) -> str | None:
        """最近一次异常(红/黄)点的颜色,用于排序。"""
        for p in reversed(points):
            c = p.get("color")
            if c in ("red", "yellow"):
                return c
        return None

    trend_items = [v for v in trend_items
                   if any(p.get("color") in ("red", "yellow") for p in v["points"])]
```

并把函数尾部的排序/返回(现约 172-179 行)`by_key.values()` 换成 `trend_items`:

```python
    trends_sorted = sorted(
        trend_items,
        key=lambda x: (_SEV.get(_abnormal_sev(x["points"]), 2), -_range(x)),
    )
```

(c) `_series`(现约 285-293 行)点 dict 补键、返回前跑守卫:

```python
        item["points"].append({
            "report_id": ind.report_id,
            "report_date": rid2date.get(ind.report_id),
            "value": str(ind.result_value).strip(),
            "color": colors.get(ind.id),
            "item_name": ind.item_name,
            "unit": ind.unit,
        })
    for item in by_key.values():
        item["points"].sort(key=lambda p: (p["report_date"] is not None, p["report_date"] or ""))
    return _split_item_name_collisions(list(by_key.values()))
```

- [ ] **Step 4: 跑测试确认通过(守卫 + 既有不回退)**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py tests/user_profile/test_change_overview.py -q`
Expected: PASS。关键不回归用例:
- `test_key_indicators_count_distinct_reports_not_rows`(同报告同名重复行**不拆**,distinct-report 门照旧排除);
- `test_series_splits_same_report_different_item_names`(不同名才拆)。

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/modules/user_profile/service.py tests/user_profile/test_change_overview.py
git commit -m "feat(profile): 走势/变化总览聚合防线——同报告多 item_name 拆独立系列并告警"
```

---

### Task 4: 存量回填脚本(重算 `item_name_standard`)

**Files:**
- Create: `backend/scripts/manual_migrations/007_fix_indicator_standard.py`

**Interfaces:**
- Consumes: `app.core.database.get_session(db_name)`、`app.core.term_normalizer.normalize_item_name`。
- Produces: 可独立运行的 CLI,默认 dry-run、`--apply` 落库;只洗 `hospital_1/hospital_H001/hospital_H002`(可用 `--db` 覆盖)。
- 依赖 Task 1 已合入的 `normalize_item_name` 新语义。

- [ ] **Step 1: 创建脚本**

```python
#!/usr/bin/env python3
"""007_fix_indicator_standard.py:按修复后 term_normalizer 词表重算 report_indicator.item_name_standard。

背景:旧词表子串匹配把血常规子项(血小板比积/平均体积/分布宽度/大血小板比率、红细胞压积/
MCV/MCH/MCHC/RDW、小而密LDL 等)及尿检/酸碱度项误并入父项标准名。本脚本逐 DISTINCT
item_name 用新词表重算并回填。

默认只处理当前解析管线产出的库;不动 hospital_H003 / hospital_H004。

用法(在 backend 目录):
    .venv/bin/python scripts/manual_migrations/007_fix_indicator_standard.py             # dry-run
    .venv/bin/python scripts/manual_migrations/007_fix_indicator_standard.py --apply      # 落库
    .venv/bin/python scripts/manual_migrations/007_fix_indicator_standard.py --db hospital_1 --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.core.database import get_session  # noqa: E402
from app.core.term_normalizer import normalize_item_name  # noqa: E402

DEFAULT_DBS = ["hospital_1", "hospital_H001", "hospital_H002"]


def plan_updates(db, db_name: str):
    """返回 [(item_name, old_standard, new_standard, rows)];按 DISTINCT item_name 汇总待改行数。"""
    rows = db.execute(text(
        "SELECT DISTINCT item_name, item_name_standard FROM report_indicator "
        "WHERE item_name IS NOT NULL AND item_name <> ''"
    )).fetchall()
    changes = []
    for item_name, old in rows:
        new, _ = normalize_item_name(item_name)
        if new == old:
            continue
        cnt = db.execute(text(
            "SELECT COUNT(*) FROM report_indicator WHERE item_name = :n AND "
            "item_name_standard IS DISTINCT FROM :new"
        ), {"n": item_name, "new": new}).scalar()
        if cnt:
            changes.append((item_name, old, new, int(cnt)))
    return changes


def apply(db, changes) -> None:
    for item_name, _old, new, _cnt in changes:
        db.execute(text(
            "UPDATE report_indicator SET item_name_standard = :new WHERE item_name = :n"
        ), {"n": item_name, "new": new})
    db.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description="按新词表重算 report_indicator.item_name_standard")
    ap.add_argument("--db", action="append", dest="dbs", default=[],
                    help="目标库名(可多次);默认 %s" % ", ".join(DEFAULT_DBS))
    ap.add_argument("--apply", action="store_true", help="实际写库;缺省为 dry-run 只打印")
    args = ap.parse_args()
    dbs = args.dbs or DEFAULT_DBS

    total = 0
    for db_name in dbs:
        db = get_session(db_name)
        try:
            changes = plan_updates(db, db_name)
            rows = sum(c[3] for c in changes)
            total += rows
            print("== %s: %d distinct item_name, %d rows to change" % (db_name, len(changes), rows))
            for item_name, old, new, cnt in sorted(changes, key=lambda c: -c[3])[:80]:
                print("   %-26s %-30s -> %-30s (%d rows)"
                      % (item_name, old or "(NULL)", new, cnt))
            if len(changes) > 80:
                print("   ... 其余 %d 项略" % (len(changes) - 80))
            if args.apply:
                apply(db, changes)
                print("   applied.")
        finally:
            db.close()
    print("TOTAL rows: %d (mode: %s)" % (total, "APPLY" if args.apply else "dry-run"))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: dry-run 预演(hospital_1)**

Run: `cd backend && .venv/bin/python scripts/manual_migrations/007_fix_indicator_standard.py --db hospital_1`
Expected: 打印 `血小板计数（PLT）` 相关 item_name 拆分;如
`血小板比积 血小板计数（PLT） -> 血小板比积（PCT） (... rows)`、`血小板平均体积 ... -> 血小板平均体积（MPV）`、
`大血小板比率 ... -> 大血小板比率（P-LCR）`、`小而密低密度脂蛋白胆固醇 ... -> 小而密低密度脂蛋白胆固醇（sdLDL）`、
`尿酸碱度 ... -> 尿酸碱度`、`尿白细胞（镜检） ... -> 尿白细胞（镜检）` 等,末尾 `TOTAL rows: N (mode: dry-run)`。

- [ ] **Step 3: 对三个库落库**

Run: `cd backend && .venv/bin/python scripts/manual_migrations/007_fix_indicator_standard.py --apply`
Expected: 三库分别打印 applied,末尾 `TOTAL rows: ... (mode: APPLY)`。

- [ ] **Step 4: 数据级验证 u_zhangsan**

Run(检查报告 23/27/1 的血小板子项现在有独立标准名):

```bash
docker exec hospital-mysql mysql -uroot -proot --default-character-set=utf8mb4 -N -e \
"SELECT report_id, item_name, item_name_standard FROM hospital_1.report_indicator \
 WHERE report_id IN (23,27,1) AND (item_name_standard LIKE '%血小板%' OR item_name LIKE '%血小板%') \
 ORDER BY report_id;" 
```

Expected: 血小板比积 → `血小板比积（PCT）`、血小板平均体积 → `血小板平均体积（MPV）`、血小板分布宽度 → `血小板分布宽度（PDW）`、大血小板比率 → `大血小板比率（P-LCR）`,仅 血小板计数 保留 `血小板计数（PLT）`。

- [ ] **Step 5: Commit**

```bash
cd backend && git add scripts/manual_migrations/007_fix_indicator_standard.py
git commit -m "feat(migration): 007 按新词表重算 hospital_1/H001/H002 的 indicator 标准名"
```

---

### Task 5: 全量回归 + 端到端复现 + 文档备注

**Files:**
- Test run(无代码改动,除非回归暴露问题)
- Modify(小): `AGENTS.md`(在 2026-09-09 change-overview 一节后补一行口径)

- [ ] **Step 1: 全量回归**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile tests/core/test_term_normalizer.py -q`
Expected: PASS(全绿;如回归暴露,修复后重跑再进下一步)。

- [ ] **Step 2: 端到端复现(get_overview 对 u_zhangsan)**

运行复现脚本(脚本放 `/tmp/opencode/repro_overview.py`,若不存在按下面代码重建):

```bash
cd /data/project/hospitalKnowledgeBase/backend && .venv/bin/python /tmp/opencode/repro_overview.py 2>&1 | head -40
```

Expected: 走势列表中**不再出现任何子项系列**(血小板比积/平均体积/分布宽度/大血小板比率);任一主项系列的每报告点数为 1。u_zhangsan 的血小板计数因 3 个窗口点(319/319/210)全绿、按「只显示窗口内红/黄指标」的既有规则**整条不出现** —— 属预期(此前 11 点的黄来自被隐藏的子项),不再断言 PLT 系列存在。

- [ ] **Step 3: AGENTS.md 补口径**

在 `AGENTS.md` 的「报告跨报告对比 → 我的页健康变化总览(2026-09-09 起)」一节末尾追加两行:

```markdown
- **指标走势只留主项(2026-09-10 起)**:血常规衍生物子项(血小板比积/PCT、平均体积/MPV、分布宽度/PDW、大血小板比率/P-LCR、红细胞压积/HCT、MCV/MCH/MCHC/RDW、小而密 LDL 等)由 `app/core/term_normalizer.py` 词表 `primary=False` 打标,`get_overview` 走势按 raw `item_name` 经 `is_child_item()` 剔除子项;`/profile/change-overview` key_indicators 与 AI 总结保留全量。`_split_item_name_collisions()` 对同报告同 key 多 item_name 的脏数据拆独立系列并告警。存量标准名回填脚本 `backend/scripts/manual_migrations/007_fix_indicator_standard.py` 已对 hospital_1/H001/H002 执行;H003/H004 旧命名库未动。
```

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(AGENTS): 指标走势只留主项 + 聚合防线 + 007 回填口径"
```
