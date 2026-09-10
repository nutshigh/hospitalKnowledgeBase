# 走势/总览展示窗口内全部红黄指标(含子项) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「指标走势」与「近期健康变化」展示窗口内出现过红/黄的**每一项指标(含血常规子项)**,各自以规范名独立成系列;不再因 `is_child_item` 隐藏子项。

**Architecture:** 仅删除 `user_profile/service.py` 里 `get_overview` 与 `_rank_key_indicators` 的两处子项过滤;共享入选/排序/上限、拆系列防线、缓存校验、数据表全部不变。

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy / MySQL 8 / pytest(SQLite in-memory)。

## Global Constraints

- 只改 `backend/app/modules/user_profile/service.py` + 两个测试文件 + `AGENTS.md`;不改配置/前端/DDL。
- 入选口径:窗口内 **出现过红/黄** 的每一项指标(含子项);正常(绿色)指标不出现。
- 子项以自身规范名(`normalize_item_name` 结果,如 `血小板比积（PCT）`)独立成系列,每报告 ≤1 点(由既有 `_split_item_name_collisions` + 标准名保证)。
- 与走势对齐的规则/排序/上限(`_has_abnormal` / `_trend_sort_key` / `PROFILE_TREND_MAX_ITEMS=10`)保持不变;前端不改。
- `app/core/term_normalizer.py::is_child_item` **保留**(含其单测),只是 profile 不再消费它。
- 抽取公共逻辑以既有函数为准;不新增无关注释/依赖。
- 测试命令:`cd backend && .venv/bin/python -m pytest <路径> -q`。

---

### Task 1: 删除子项过滤(service + 测试反转)

**Files:**
- Modify: `backend/app/modules/user_profile/service.py`
- Test: `backend/tests/user_profile/test_service.py`
- Test: `backend/tests/user_profile/test_change_overview.py`

**Interfaces:**
- Consumes: 既有 `_has_abnormal` / `_trend_sort_key` / `_split_item_name_collisions` / `PROFILE_TREND_MAX_ITEMS`。
- Produces: `get_overview.indicator_trends` 与 `_rank_key_indicators` 均不再过滤子项;异常子项以规范名出现。

- [ ] **Step 1: 写失败测试**(先把「只留主项」用例反转为「异常子项必须出现」)

(a) `backend/tests/user_profile/test_service.py`:把现有 `test_get_overview_trends_only_primary_items`(整段,含其 docstring 与函数体)整体替换为:

```python
def test_get_overview_trends_include_abnormal_child_items(db):
    """窗口内有红/黄判定的子项也必须出现,且以规范名独立成系列;无判定子项不出现。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment

    parent = "血小板计数"
    child_abn = "血小板比积"        # 带黄判定 → 应出现
    child_quiet = "血小板平均体积"  # 无判定 → 不出现
    for rid, dt, pv, cv in [(1, date(2024, 6, 1), "300", "0.29"),
                            (2, date(2025, 6, 1), "319", "0.31"),
                            (3, date(2026, 6, 1), "210", "0.33")]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
        db.add(ReportIndicator(id=rid * 100 + 1, report_id=rid, item_name=parent,
                               item_name_standard="血小板计数（PLT）",
                               result_value=pv, unit="x10^9/L"))
        db.add(ReportIndicator(id=rid * 100 + 2, report_id=rid, item_name=child_abn,
                               item_name_standard="血小板计数（PLT）",
                               result_value=cv, unit="%"))
        db.add(ReportIndicator(id=rid * 100 + 3, report_id=rid, item_name=child_quiet,
                               item_name_standard="血小板计数（PLT）",
                               result_value="9.1", unit="fL"))
    db.commit()
    # 父项与异常子项各给一点黄判定(报告3)
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=301, item_name=parent,
                             color_level="yellow"))
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=302, item_name=child_abn,
                             color_level="yellow"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    by_std = {t["item_name_standard"]: t for t in result["indicator_trends"]}
    assert "血小板计数（PLT）" in by_std
    assert "血小板比积（PCT）" in by_std          # 异常子项出现
    assert "血小板平均体积（MPV）" not in by_std  # 无判定子项不出现
    assert len(by_std["血小板计数（PLT）"]["points"]) == 3
    assert len({p["report_id"] for p in by_std["血小板比积（PCT）"]["points"]}) == 3
```

(b) `backend/tests/user_profile/test_change_overview.py`:把现有 `test_key_indicators_exclude_child_items`(整段)整体替换为:

```python
def test_key_indicators_include_abnormal_child_items(db):
    """窗口内红/黄的子项必须进 key_indicators(规范名);主项异常同样保留。"""
    from app.modules.user_profile.service import get_change_overview

    for rid in (1, 2):
        _report(db, rid, rdate=date(2025, rid, 1))
        _indicator(db, rid, rid, "血糖", "空腹血糖", "6.0")
        _indicator(db, rid * 10 + 1, rid, "血小板比积", "血小板比积（PCT）", "0.29", "%")
        _completed(db, rid)
    _judgment(db, 1, 1, 1, "yellow")
    _judgment(db, 2, 2, 2, "yellow")
    _judgment(db, 3, 1, 11, "yellow")
    _judgment(db, 4, 2, 21, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    assert len(names) == 2
    assert set(names) == {"空腹血糖", "血小板比积（PCT）"}
```

(c) 在 `backend/tests/user_profile/test_change_overview.py` 末尾追加一条跨端点一致性用例:

```python
def test_overview_and_change_include_abnormal_child_parity(db):
    """同一窗口下两处列表一致,且都含异常子项(规范名),每系列每报告 ≤1 点。"""
    from app.modules.user_profile.service import get_overview, get_change_overview

    for rid, rdate in [(1, date(2025, 5, 1)), (2, date(2026, 5, 1))]:
        _report(db, rid, rdate=rdate)
        _completed(db, rid)
        _indicator(db, rid, rid, "血糖", "空腹血糖（GLU）", "6.0")
        _indicator(db, rid * 10 + 1, rid, "血小板比积", "血小板比积（PCT）", "0.29", "%")
    _judgment(db, 1, 1, 1, "yellow")
    _judgment(db, 2, 2, 2, "yellow")
    _judgment(db, 3, 1, 11, "yellow")
    _judgment(db, 4, 2, 21, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        co = get_change_overview(db, "123456", "张三")
    ov = get_overview(db, "123456", "张三")

    ov_names = [t["item_name_standard"] for t in ov["indicator_trends"]]
    co_names = [k["item_name_standard"] for k in co["key_indicators"]]
    assert "血小板比积（PCT）" in ov_names and "血小板比积（PCT）" in co_names
    assert ov_names == co_names
    for t in ov["indicator_trends"]:
        reps = [p["report_id"] for p in t["points"]]
        assert len(reps) == len(set(reps))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py::test_get_overview_trends_include_abnormal_child_items tests/user_profile/test_change_overview.py::test_key_indicators_include_abnormal_child_items -q`
Expected: FAIL —— 子项仍被 `is_child_item` 过滤,断言 `"血小板比积（PCT）" in ...` 不成立。

- [ ] **Step 3: 删除两处子项过滤**

`backend/app/modules/user_profile/service.py`:

(a) 顶部导入(现 line 13)改为:

```python
from app.core.term_normalizer import normalize_item_name
```

(b) `get_overview` 指标循环(现约 line 123-124)删除这两行:

```python
        if is_child_item(ind.item_name or ""):
            continue
```

(c) `_rank_key_indicators`(现约 line 382-388)删除 `standard` 变量与子项判断,保留红黄门:

```python
    ranked = []
    for item in _series(db, window):
        points = item["points"]
        if not _has_abnormal(points):
            continue
```

(该函数其余部分与 `standard` 无关,删除后不得残留未使用变量。)

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile tests/core/test_term_normalizer.py -q`
Expected: PASS(反转的 2 条 + 新增 1 条 + 既有全部;既有「上限 10」「排序/拆系列」「缓存 fingerprint」「is_child_item 单测」保持通过)。

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/modules/user_profile/service.py tests/user_profile/test_service.py tests/user_profile/test_change_overview.py
git commit -m "feat(profile): 走势/总览展示窗口内全部红黄指标(含子项),不再隐藏子项"
```

---

### Task 2: 文档口径更新 + 清缓存 + 端到端验证

**Files:**
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: Task 1 行为。
- Produces: 上线步骤(再清一次 008 缓存 + 重启 :8000)落实与文档一致。

- [ ] **Step 1: 更新 AGENTS.md 两条口径**

(a) 把 `AGENTS.md:231` 的整个 bullet(`- **指标走势只留主项(2026-09-10 起)**:...H003/H004 旧命名库未动。`)替换为:

```markdown
- **走势/变化总览展示窗口内全部红黄指标(含子项)(2026-09-10 起)**:窗口内出现过红/黄的每一项指标(含血常规子项,如 血小板比积（PCT）、血小板平均体积（MPV）)都以自身规范名在 `get_overview` 走势与 `/profile/change-overview` 的 `key_indicators` 独立成系列,每报告 ≤1 点;两处入选/排序/上限一致(最近异常红>黄再极差降序、`PROFILE_TREND_MAX_ITEMS` 默认 10)。正常(绿色)指标不出现(如全绿的血小板计数)。`app/core/term_normalizer.py::is_child_item` 保留但其定义不再被 profile 用于过滤;`_split_item_name_collisions()` 仍对同报告同 key 多 item_name 的脏数据拆独立系列并告警。存量标准名回填脚本 007 已对 hospital_1/H001/H002 执行;H003/H004 未回填。
```

(b) 把 `AGENTS.md` 2026-09-10「变化总览/走势取窗差异(已知)」bullet 中这一句:

```
且 AI 总结的输入指标集合已随之改变(子项不再进入、单报告异常会进入)。
```

改为:

```
且 AI 总结的输入指标集合已随之改变(异常子项也会进入、单报告异常会进入)。
```

- [ ] **Step 2: 再清一次变化总览缓存**

Run(逐库):

```bash
cd /data/project/hospitalKnowledgeBase
for db in hospital_1 hospital_H001 hospital_H002 hospital_H003 hospital_H004; do
  docker exec -i hospital-mysql mysql -uroot -proot --default-character-set=utf8mb4 "$db" \
    < backend/scripts/manual_migrations/008_clear_change_overview_cache.sql
done
for db in hospital_1 hospital_H001 hospital_H002 hospital_H003 hospital_H004; do
  docker exec hospital-mysql mysql -uroot -proot -N -e \
    "SELECT COUNT(*) FROM $db.report_interpretation WHERE comparison_summary IS NOT NULL;" 2>/dev/null \
    | sed "s/^/$db /"
done
```

Expected: 各库计数 `0`。

- [ ] **Step 3: 端到端验证(u_zhangsan,新代码)**

Run:

```bash
cd /data/project/hospitalKnowledgeBase/backend && .venv/bin/python /tmp/opencode/check_platelet.py 2>&1 | grep -v Warning | grep -v warnings.warn
```

(`/tmp/opencode/check_platelet.py` 打印 hospital_1 u_zhangsan 的 `indicator_trends` 与 `key_indicators` 名称列表;若缺失可现场写等价脚本。)

Expected: 两处列表一致,且**包含** `血小板比积（PCT）` 与 `血小板平均体积（MPV）`(即子项异常重新出现);`血小板计数（PLT）` 因全绿仍不出现;每系列每报告 ≤1 点。

- [ ] **Step 4: 全量回归**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile tests/core/test_term_normalizer.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs(AGENTS): 走势/总览展示全部红黄指标(含子项)口径 + 再清缓存"
```

---

## 上线后人工步骤(不入代码)

- 重启 `:8000` backend,否则旧进程仍按旧口径写 `comparison_summary`。
- 浏览器查看「我的」页:确认走势/总览出现异常子项线(如 `PCT`/`MPV`),两处列表一致。
