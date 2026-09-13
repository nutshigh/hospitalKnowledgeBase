# 变化总览与指标走势 入选规则/条数对齐 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「近期健康变化」的 key_indicators 与「指标走势」用同一套入选/排序规则(仅主项 + 窗口内红/黄)、同一 10 条上限;一次清理旧缓存。

**Architecture:** 在 `user_profile/service.py` 抽共享模块级 `_has_abnormal` / `_points_range` / `_trend_sort_key`(复用既有 `_severity`),`get_overview` 与 `_rank_key_indicators` 都用它;新增配置 `PROFILE_TREND_MAX_ITEMS=10` 供两处截断;新增一次性清缓存 SQL。

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy / MySQL 8 / pytest(SQLite in-memory)。

## Global Constraints

- 依赖 pin 不变:不得改动 `backend/pyproject.toml` / uv.lock / venv。
- 取窗口径**不变**:走势=最近 N 份报告(不限解读状态);总览=最近 N 份**已完成解读**报告。
- 入选口径统一为:**仅主项**(`is_child_item` 过滤子项)+ **窗口内任一点红/黄**;去掉「≥2 份报告」与「|delta_pct|≥5」两个门。
- 排序统一为 `(_severity, -极差, item_name)`(最近异常红=0 > 黄=1 > 无=2;同级按极差降序,再按名)。
- 上限统一 `settings.PROFILE_TREND_MAX_ITEMS`(默认 10);前端不改代码(`ChangeOverviewCard` 仍 5 条折叠+展开;`ProfilePage` 仍 slice 10)。
- 不加缓存版本 key;旧缓存靠一次性 SQL 清理。`build_change_prompt` 内部 `[:8]` 不改。
- 所有测试从 `backend` 目录运行:`cd backend && .venv/bin/python -m pytest <路径> -q`。

---

### Task 1: 共享入选/排序工具 + `get_overview` 对齐/限流 + 配置

**Files:**
- Modify: `backend/app/config.py`(新增配置字段)
- Modify: `backend/.env.example`(注释)
- Modify: `backend/app/modules/user_profile/service.py`(共享工具 + `get_overview`)
- Test: `backend/tests/user_profile/test_service.py`(追加限流用例)

**Interfaces:**
- Consumes: 既有 `_severity(points) -> int`、`_try_float`、`settings`。
- Produces:
  - `settings.PROFILE_TREND_MAX_ITEMS: int`(默认 10)。
  - `_has_abnormal(points: list[dict]) -> bool`
  - `_points_range(points: list[dict]) -> float`
  - `_trend_sort_key(item: dict) -> tuple`

- [ ] **Step 1: 写失败测试**(`get_overview` 条数上限)

在 `backend/tests/user_profile/test_service.py` 末尾追加:

```python
def test_get_overview_trends_capped_at_config_limit(db):
    """窗口内红/黄主项超过上限(默认10)→ indicator_trends 截断为 10。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment
    from app.config import settings

    for rid in (1, 2):
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=date(2025, rid, 1)))
    db.commit()
    # 12 个不同的主项标准名,各在报告2 带黄判定
    for i in range(12):
        std = "指标%02d" % i
        db.add(ReportIndicator(id=100 + i, report_id=1, item_name=std,
                               item_name_standard=std, result_value="1.0", unit=""))
        db.add(ReportIndicator(id=200 + i, report_id=2, item_name=std,
                               item_name_standard=std, result_value="2.0", unit=""))
    db.commit()
    for i in range(12):
        db.add(IndicatorJudgment(interpretation_id=99, indicator_id=200 + i,
                                 item_name="x", color_level="yellow"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    assert settings.PROFILE_TREND_MAX_ITEMS == 10
    assert len(result["indicator_trends"]) == 10
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py::test_get_overview_trends_capped_at_config_limit -q`
Expected: FAIL —— `AttributeError: 'Settings' object has no attribute 'PROFILE_TREND_MAX_ITEMS'`(或 `len(...) == 12 != 10`)。

- [ ] **Step 3: 加配置**

`backend/app/config.py` 在 `PROFILE_TREND_REPORT_LIMIT` 行后新增:

```python
    # User Profile(用户端):指标走势 / 变化总览关键指标最多展示条数
    PROFILE_TREND_MAX_ITEMS: int = Field(default=10, ge=1, description="指标走势/变化总览关键指标最多展示条数")
```

`backend/.env.example` 在 `# PROFILE_TREND_REPORT_LIMIT=3` 附近加一行:

```
# PROFILE_TREND_MAX_ITEMS=10
```

- [ ] **Step 4: 加共享工具并改 `get_overview`**

在 `service.py` 的 `_severity` 函数(现约 line 360)之后新增三个模块级函数:

```python
def _has_abnormal(points: list[dict]) -> bool:
    """窗口内任一点红/黄。"""
    return any(p.get("color") in ("red", "yellow") for p in points)


def _points_range(points: list[dict]) -> float:
    """数值极差 max-min;经 _try_float 兼容 float(get_overview) 与 str(_series);
    无有效数值返回 0.0。"""
    vals = [v for v in (_try_float(p.get("value")) for p in points) if v is not None]
    return max(vals) - min(vals) if vals else 0.0


def _trend_sort_key(item: dict):
    """统一排序键:最近异常红>黄,同级按极差降序,再按指标名。"""
    pts = item["points"]
    return (_severity(pts), -_points_range(pts), item.get("item_name") or "")
```

改 `get_overview` 的两处:

(a) 现约 line 145-160 的「排序 + 内嵌 `_abnormal_sev` + 红黄过滤」块,整体替换为:

```python
    trend_items = _split_item_name_collisions(list(by_key.values()))
    for v in trend_items:
        v["points"].sort(key=lambda p: (p["report_date"] is not None, p["report_date"] or ""))
        v["trend_direction"] = trend_direction(v["points"])
        v["latest_deviation"] = v["points"][-1].get("color") if v["points"] else None

    trend_items = [v for v in trend_items if _has_abnormal(v["points"])]
```

(b) 现约 line 207-221 的 `_SEV` / 内嵌 `_range` / `trends_sorted` / return,替换为:

```python
    trends_sorted = sorted(trend_items, key=_trend_sort_key)
    return {
        "user_summary": summary,
        "indicator_trends": trends_sorted[:settings.PROFILE_TREND_MAX_ITEMS],
        "abnormal_distribution": abnormal_distribution,
    }
```

(注意:删除内嵌 `_abnormal_sev`、`_SEV`、`_range` 定义;`_severity` 现为模块级已存在,勿重复定义。)

- [ ] **Step 5: 跑测试确认通过 + 回归**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py tests/user_profile/test_change_overview.py -q`
Expected: PASS(新增限流用例 + 既有;既有红黄顺序用例因红/黄不同级不受 item_name 兜底影响)。

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/config.py .env.example app/modules/user_profile/service.py tests/user_profile/test_service.py
git commit -m "feat(profile): 提取共享入选/排序工具 + 走势限流 PROFILE_TREND_MAX_ITEMS"
```

---

### Task 2: `_rank_key_indicators` 对齐走势口径 + 总览限流

**Files:**
- Modify: `backend/app/modules/user_profile/service.py`(`_rank_key_indicators` + `get_change_overview`)
- Test: `backend/tests/user_profile/test_change_overview.py`(改写 2 条、新增 2 条)

**Interfaces:**
- Consumes: Task 1 的 `_has_abnormal` / `_trend_sort_key` / `settings.PROFILE_TREND_MAX_ITEMS`;既有 `_series`、`_severity`、`_endpoint_pct`、`is_child_item`。
- Produces: `_rank_key_indicators` 返回与走势同口径、同排序的完整候选列表;`get_change_overview.key_indicators` 截断到 `PROFILE_TREND_MAX_ITEMS`。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/user_profile/test_change_overview.py` 的「关键指标」区(`# ---------- 关键指标 ----------` 段落内)替换旧的两条用例为新口径,并新增两条。整段替换 `test_key_indicators_exclude_single_report_and_sort_red_first` 与 `test_key_indicators_count_distinct_reports_not_rows`(自 `def test_key_indicators_exclude_single_report_and_sort_red_first` 起到下一个 `# ---------- worker 钩子 ----------` 之前)为:

```python
def test_key_indicators_single_report_included_and_sorted(db):
    """新口径:单份报告出现的异常指标也入选(delta_pct=None);排序按最近异常红>黄、极差降序。"""
    from app.modules.user_profile.service import get_change_overview

    # 报告1、2 都有血糖;报告2 独有尿酸(仅 1 份,新口径应入选)
    _report(db, 1, rdate=date(2025, 5, 1))
    _indicator(db, 1, 1, "血糖", "空腹血糖", "7.2")
    _report(db, 2, rdate=date(2026, 5, 1))
    _indicator(db, 2, 2, "血糖", "空腹血糖", "6.4")
    _indicator(db, 3, 2, "尿酸", "尿酸", "420", "μmol/L")
    _completed(db, 1, level="red", red=1)
    _completed(db, 2, level="yellow", yellow=1)
    _judgment(db, 1, 1, 1, "red")
    _judgment(db, 2, 2, 2, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    # 血糖极差 0.8 > 尿酸 0 → 血糖在前;尿酸单报告也入选
    assert names == ["空腹血糖", "尿酸"]
    by = {k["item_name"]: k for k in result["key_indicators"]}
    assert by["空腹血糖"]["latest_color"] == "yellow"
    assert by["空腹血糖"]["delta_pct"] == pytest.approx(-11.11, abs=0.1)
    assert by["尿酸"]["delta_pct"] is None


def test_key_indicators_same_report_duplicate_rows_stay_one_series(db):
    """同报告同名重复行仍是一条系列(不因行数被排除/拆分);窗口内异常即入选。"""
    from app.modules.user_profile.service import get_change_overview

    _report(db, 1, rdate=date(2025, 5, 1))
    _indicator(db, 1, 1, "血糖", "空腹血糖", "6.0")
    _indicator(db, 2, 1, "血糖", "空腹血糖", "6.8")
    _indicator(db, 3, 1, "血脂", "血脂", "3.1", "mmol/L")
    _indicator(db, 5, 1, "血脂", "血脂", "3.3", "mmol/L")
    _report(db, 2, rdate=date(2026, 5, 1))
    _indicator(db, 4, 2, "血糖", "空腹血糖", "6.4")
    _completed(db, 1, level="red", red=1)
    _completed(db, 2, level="yellow", yellow=1)
    _judgment(db, 1, 1, 1, "red")
    _judgment(db, 2, 1, 2, "red")
    _judgment(db, 3, 1, 3, "red")
    _judgment(db, 5, 1, 5, "yellow")
    _judgment(db, 4, 2, 4, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    assert names == ["空腹血糖", "血脂"]  # 血脂(单报告双行)按新口径入选,且只一条


def test_key_indicators_exclude_child_items(db):
    """子项(血常规衍生物)不进 key_indicators;主项异常正常入选。"""
    from app.modules.user_profile.service import get_change_overview

    for rid in (1, 2):
        _report(db, rid, rdate=date(2025, rid, 1))
        _indicator(db, rid, rid, "血糖", "空腹血糖", "6.0")
        # 子项:raw 名 血小板比积,标准名已是 canonical child
        _indicator(db, rid * 10 + 1, rid, "血小板比积", "血小板比积（PCT）", "0.29", "%")
        _completed(db, rid)
    _judgment(db, 1, 1, 1, "yellow")
    _judgment(db, 2, 2, 2, "yellow")
    _judgment(db, 3, 1, 11, "yellow")   # 子项黄判定
    _judgment(db, 4, 2, 21, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    assert names == ["空腹血糖"]  # 血小板比积（PCT）子项被过滤


def test_change_overview_key_indicators_capped_at_config_limit(db):
    """窗口内红/黄主项超过上限(默认10)→ key_indicators 截断为 10。"""
    from app.modules.user_profile.service import get_change_overview
    from app.config import settings

    for rid in (1, 2):
        _report(db, rid, rdate=date(2025, rid, 1))
        _completed(db, rid)
    for i in range(12):
        std = "指标%02d" % i
        _indicator(db, 100 + i, 1, std, std, "1.0", "")
        _indicator(db, 200 + i, 2, std, std, "2.0", "")
        _judgment(db, 300 + i, 2, 200 + i, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    assert settings.PROFILE_TREND_MAX_ITEMS == 10
    assert len(result["key_indicators"]) == 10
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_change_overview.py -q`
Expected: FAIL —— 例如 `names == ["空腹血糖", "尿酸"]` 实际为 `["空腹血糖"]`(旧「≥2 份」门把尿酸排除);子项用例中 `血小板比积（PCT）` 未被过滤。

- [ ] **Step 3: 重写 `_rank_key_indicators` 并改 `get_change_overview`**

`service.py` 的 `_rank_key_indicators`(现约 line 376-405)整体替换为:

```python
def _rank_key_indicators(db: Session, window: list) -> list[dict]:
    """关键指标:与指标走势同一套口径 —— 仅主项、窗口内任一点红/黄;排序同走势。

    不再要求 ≥2 份报告,也不再需要 |delta_pct|≥5;子项(血常规衍生物等)不入选。
    """
    ranked = []
    for item in _series(db, window):
        points = item["points"]
        standard = item.get("item_name_standard") or item.get("item_name") or ""
        if is_child_item(standard):
            continue
        if not _has_abnormal(points):
            continue
        ranked.append({
            "item_name": item["item_name"],
            "unit": item["unit"],
            "latest_value": points[-1]["value"],
            "latest_color": points[-1]["color"],
            "direction": trend_direction(points),
            "delta_pct": _endpoint_pct(points),
            "points": points,
        })
    ranked.sort(key=_trend_sort_key)
    return ranked
```

`get_change_overview`(现约 line 459-465)payload 的 `"key_indicators": key_indicators[:5],` 改为:

```python
        "key_indicators": key_indicators[:settings.PROFILE_TREND_MAX_ITEMS],
```

(`prompt = build_change_prompt(reports, key_indicators)` 保持传完整候选列表,其内部 `[:8]` 不变。)

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile -q`
Expected: PASS(改写 2 条 + 新增 2 条 + 既有;`test_generate_writes_cache_and_second_call_hits` 仍 `len==1`;`test_series_splits...` 不受影响)。

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/modules/user_profile/service.py tests/user_profile/test_change_overview.py
git commit -m "feat(profile): 变化总览关键指标对齐走势口径与10条上限,子项不入选"
```

---

### Task 3: 一次性清缓存 + 文档 + 端到端一致性验证

**Files:**
- Create: `backend/scripts/manual_migrations/008_clear_change_overview_cache.sql`
- Modify: `AGENTS.md`(2026-09-10 备注段)

**Interfaces:**
- Consumes: Task 1/2 行为。
- Produces: 清空后的缓存下次访问自动重算;两处列表同口径。

- [ ] **Step 1: 创建清缓存 SQL**

`backend/scripts/manual_migrations/008_clear_change_overview_cache.sql`:

```sql
-- 清空「近期健康变化」缓存(JSON)与退役的旧双报告对比纯文本。
-- 属可再生缓存:下次访问 /profile/change-overview 会自动重算写回,无数据损失。
-- 2026-09-10:入选规则/排序/条数改为与指标走势一致,旧缓存口径已过时,需一次性清理。
UPDATE report_interpretation SET comparison_summary = NULL WHERE comparison_summary IS NOT NULL;
```

- [ ] **Step 2: 对全部 tenant 库执行一次**

Run(逐库):

```bash
for db in hospital_1 hospital_H001 hospital_H002 hospital_H003 hospital_H004; do
  echo "== $db =="
  docker exec -i hospital-mysql mysql -uroot -proot --default-character-set=utf8mb4 "$db" \
    < backend/scripts/manual_migrations/008_clear_change_overview_cache.sql
done
```

Expected: 每库打印 `== hospital_x ==`,无报错(受影响行数可忽略)。执行后抽查:

```bash
for db in hospital_1 hospital_H001 hospital_H002 hospital_H003 hospital_H004; do
  docker exec hospital-mysql mysql -uroot -proot -N -e \
    "SELECT COUNT(*) FROM $db.report_interpretation WHERE comparison_summary IS NOT NULL;" 2>/dev/null \
    | sed "s/^/$db /"
done
```
Expected: 每行计数 `0`。

- [ ] **Step 3: 端到端一致性验证(hospital_1 / u_zhangsan)**

运行 `/tmp/opencode/compare_two.py`(若不存在则重建:`get_overview(db,"011234","张三")` 打印 indicator_trends 名称列表;`get_change_overview(db,"011234","张三")` 打印 key_indicators 名称列表):

```bash
cd /data/project/hospitalKnowledgeBase/backend && .venv/bin/python /tmp/opencode/compare_two.py 2>&1 | grep -v Warning | grep -v warnings.warn
```

Expected: 两处指标名列表**完全一致**(u_zhangsan 两窗口均为 reports 23/27/1);`key_indicators` 不再出现子项、不再受 5 条/≥2 份/|delta| 门限制。
注意:本机 `LLM is explicitly disabled. Using MockLLM.`,总结解析失败 → `summary=None` → **不写缓存**,故 `cached` 会一直是 `False`,这是预期,不要当作失败;一致性以两份名称列表为准。

- [ ] **Step 4: 更新 AGENTS.md**

`AGENTS.md:231` 的「指标走势只留主项(2026-09-10 起)」这条 bullet 中,把子句 `;`/`。` 处的表述精确替换。

oldString(该 bullet 内片段):

```
`get_overview` 走势按 raw `item_name` 经 `is_child_item()` 剔除子项;`/profile/change-overview` key_indicators 与 AI 总结保留全量。
```

newString:

```
`get_overview` 走势按 raw `item_name` 经 `is_child_item()` 剔除子项;`/profile/change-overview` 的 `key_indicators` 自 2026-09-10 起与走势同口径同上限(仅主项、窗口内任一点红/黄、按最近异常红>黄再极差降序、`PROFILE_TREND_MAX_ITEMS` 默认 10 截断),不再保留全量。
```

再在该 bullet 之后新增一条(位于 `---` 分隔线之前):

```markdown
- **变化总览/走势取窗差异(已知)**:走势取最近 N 份报告(不限解读状态);变化总览取最近 N 份**已完成解读**报告。两者入选规则/排序/上限已对齐(2026-09-10),取窗仍可能不同。规则口径变更后需一次性清空存量缓存:`backend/scripts/manual_migrations/008_clear_change_overview_cache.sql`(对全部 tenant 库各执行一次)。
```

- [ ] **Step 5: 全量回归**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile tests/core/test_term_normalizer.py -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/manual_migrations/008_clear_change_overview_cache.sql AGENTS.md
git commit -m "chore(profile): 008 清理变化总览缓存 + AGENTS 口径更新"
```
