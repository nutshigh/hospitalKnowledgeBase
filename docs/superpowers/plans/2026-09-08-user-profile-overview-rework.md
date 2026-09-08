# 用户端 Overview 改造(走势限流/移除异常分布/走势折叠) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户端「我的健康档案」`/profile/overview` 指标走势只纳入最近 N 份报告(默认 3,可配置);前端移除「异常指标分布」卡片;走势条目默认收起、点击多选展开趋势图。

**Architecture:** 后端 `service.py::get_overview` 在现有升序 `reports` 上取 `reports[-N:]` 作为走势子集,只对该子集查指标/取点/算方向;`user_summary` 与 `abnormal_distribution`(SQL 聚合)保持全量。前端 `ProfilePage.tsx` 删除异常分布卡片渲染,用本地 `expanded: number[]` state 做多选手风琴。纯展示层 + 一处排序/子集改动,无表结构变更。

**Tech Stack:** FastAPI / SQLAlchemy / pytest(SQLite in-memory)/ React(antd)。

## Global Constraints

- 配置字段名:`PROFILE_TREND_REPORT_LIMIT`,默认 `3`,经 pydantic-settings 读 `.env`,`extra="ignore"`。
- 「最近 N 份」口径:按 `report_date` 升序后的末尾 N 份(**无日期报告在 MySQL/SQLite 升序中都排最前**,天然垫最旧被排除);有日期报告不足 N 份时退化为全量。
- `abnormal_distribution` 后端 SQL 与返回字段**保留**,仅前端不消费。
- 不改变点色语义(绿/黄 = 当份报告是否超参考范围)、`↑↓`(最后两点比较)、走势排序规则、`get_overview` 函数签名与 `/overview` 路由。
- 前端用现有内联样式,不引入新 UI 库(可用 antd 已有 `Spin/Input`,不新增 Collapse)。
- 提交风格参考仓库现有 `feat:`/`docs:` 前缀。

---

### Task 1: 后端 — 走势只纳入最近 N 份报告(配置 + service + 测试)

**Files:**
- Modify: `backend/app/config.py:137-139`(File Storage 段后、`model_config` 前加配置)
- Modify: `backend/app/modules/user_profile/service.py:62-82`(`get_overview` 内走势子集)
- Test: `backend/tests/user_profile/test_service.py`(文件末尾追加 4 个用例)

**Interfaces:**
- Consumes: `app/config.py::settings`(现有单例,`from app.config import settings`);现有 `get_overview(db, user_id, name)`。
- Produces: `get_overview` 行为变化 —— `indicator_trends[].points` 只含最近 `PROFILE_TREND_REPORT_LIMIT` 份报告的点;`user_summary` / `abnormal_distribution` 不变。新增配置 `settings.PROFILE_TREND_REPORT_LIMIT: int`。

- [ ] **Step 1: 写失败测试(4 条追加到 `backend/tests/user_profile/test_service.py` 文件末尾)**

```python
# ============================================================
# get_overview 走势限流(2026-09-08):indicator_trends 只取最近
# PROFILE_TREND_REPORT_LIMIT(默认3)份报告;user_summary/abnormal_distribution 仍全量
# ============================================================


def test_get_overview_trends_only_include_recent_n_reports(db):
    """5 份报告(2022..2026)→ 走势只含最近 3 份(2024/2025/2026)。"""
    from app.modules.user_profile.service import get_overview

    for rid, dt, val in [
        (1, date(2022, 5, 1), "5.5"),
        (2, date(2023, 5, 1), "6.0"),
        (3, date(2024, 5, 1), "6.5"),
        (4, date(2025, 5, 1), "7.0"),
        (5, date(2026, 5, 1), "7.4"),
    ]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
        db.add(ReportIndicator(report_id=rid, item_name="血糖", item_name_standard="空腹血糖",
                               result_value=val, unit="mmol/L"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "空腹血糖")
    assert [p["report_date"] for p in trend["points"]] == ["2024-05-01", "2025-05-01", "2026-05-01"]
    assert result["user_summary"]["total_reports"] == 5


def test_get_overview_trends_keep_all_when_fewer_than_limit(db):
    """只有 2 份(< 默认3)时走势仍含全部,行为与现状一致。"""
    from app.modules.user_profile.service import get_overview

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2025, 5, 1)))
    db.add(ReportInfo(id=2, user_id="123456", name="张三", report_date=date(2026, 5, 1)))
    db.add(ReportIndicator(report_id=1, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="6.0", unit="mmol/L"))
    db.add(ReportIndicator(report_id=2, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="6.8", unit="mmol/L"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "空腹血糖")
    assert len(trend["points"]) == 2


def test_get_overview_trends_exclude_null_dated_report(db):
    """6 份(5 有日期 + 1 无日期)→ 走势为最近 3 份有日期的,无日期那份垫最旧被排除。"""
    from app.modules.user_profile.service import get_overview

    for rid, dt in [(1, date(2022, 5, 1)), (2, date(2023, 5, 1)), (3, date(2024, 5, 1)),
                    (4, date(2025, 5, 1)), (5, date(2026, 5, 1))]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
    db.add(ReportInfo(id=6, user_id="123456", name="张三", report_date=None))
    for rid in range(1, 7):
        db.add(ReportIndicator(report_id=rid, item_name="血糖", item_name_standard="空腹血糖",
                               result_value="7.0", unit="mmol/L"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "空腹血糖")
    dates = [p["report_date"] for p in trend["points"]]
    assert dates == ["2024-05-01", "2025-05-01", "2026-05-01"]
    assert None not in dates


def test_get_overview_abnormal_distribution_includes_outside_trend_window(db):
    """最旧报告(2023,在走势窗口外)有红判定 → abnormal_distribution 仍计入;走势不含它。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment

    for rid, dt in [(1, date(2023, 5, 1)), (2, date(2024, 5, 1)),
                    (3, date(2025, 5, 1)), (4, date(2026, 5, 1))]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
        db.add(ReportIndicator(id=100 + rid, report_id=rid, item_name="血糖",
                               item_name_standard="空腹血糖", result_value="7.0", unit="mmol/L"))
    db.commit()
    db.add(ReportInterpretation(id=1, report_id=1, overall_level="red", status="completed",
                                red_count=1, yellow_count=0, green_count=0))
    db.add(IndicatorJudgment(interpretation_id=1, indicator_id=101, item_name="血糖", color_level="red"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    assert any(a["item_name_standard"] == "空腹血糖" and a["red_count"] == 1
               for a in result["abnormal_distribution"])
    trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "空腹血糖")
    assert [p["report_date"] for p in trend["points"]] == ["2024-05-01", "2025-05-01", "2026-05-01"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py -q -k "recent_n or fewer_than_limit or null_dated or outside_trend_window"`
Expected: FAIL —— `trend["points"]` 目前含全部 5/4/6 个点,断言列表长度/内容不匹配。

- [ ] **Step 3: 最小实现(两处改动)**

`backend/app/config.py`,在 File Storage 段(第 138 行 `FILE_STORAGE_ROOT`)之后、`model_config`(第 140 行)之前加:

```python
    # User Profile(用户端 overview):指标走势只纳入最近 N 份报告
    PROFILE_TREND_REPORT_LIMIT: int = 3
```

`backend/app/modules/user_profile/service.py`:
1. 顶部 import 区(第 5 行 `from sqlalchemy.orm import Session` 附近)加 `from app.config import settings`:

```python
from app.config import settings
```

2. `get_overview` 第 71-74 行(`report_ids` 定义处)改成:

```python
    trend_report_ids = [r.id for r in reports[-settings.PROFILE_TREND_REPORT_LIMIT:]]
    indicators = db.query(ReportIndicator).filter(
        ReportIndicator.report_id.in_(trend_report_ids),
    ).all()
```

(`reports` 已按 `report_date.asc()` 排好,切片取末尾 N 份即最近 N 次;`report_ids` 变量删除后确认无其它引用 —— 它在 get_overview 中仅用于 indicators 过滤。)

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py -q`
Expected: PASS —— 含既有用例(2 份报告的不受影响)与新增 4 条全部绿。

- [ ] **Step 5: 提交**

```bash
git add backend/app/config.py backend/app/modules/user_profile/service.py backend/tests/user_profile/test_service.py
git commit -m "feat: 用户端overview指标走势只取最近N份报告(默认3,可配置)"
```

---

### Task 2: 前端 — ProfilePage 移除异常分布卡片 + 走势行默认折叠多选展开

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ProfilePage.tsx`

**Interfaces:**
- Consumes: Task 1 的接口返回(`indicator_trends` 现只含最近 N 份点;`abnormal_distribution` 仍在响应但前端不读)。后端不新增字段,前端类型同步调整。
- Produces: 页面视觉:①「异常指标分布」卡片消失;②走势每行默认收起,头行可点开(`▸/▾`),多选展开;点 <2 的指标行不可展开。

- [ ] **Step 1: 删除异常指标分布相关类型与渲染块**

编辑 `frontend/packages/user-portal/src/pages/ProfilePage.tsx`:

(a) 删除第 32-37 行 `AbnormalDist` 接口定义,以及第 42 行 `OverviewResponse` 里 `abnormal_distribution: AbnormalDist[];` 字段:

```ts
interface OverviewResponse {
  user_summary: UserSummary | null;
  indicator_trends: IndicatorTrend[];
}
```

(b) 删除第 114-134 行整段异常指标分布卡片:

```tsx
      {data.abnormal_distribution.length > 0 && (
        <div style={{
          background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
          padding: '16px 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 16,
        }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>异常指标分布</div>
          {data.abnormal_distribution.map((a, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '8px 0', borderBottom: i < data.abnormal_distribution.length - 1 ? '1px solid var(--color-border-light)' : 'none',
              fontSize: 13,
            }}>
              <span>{a.item_name_standard}</span>
              <span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>
                红 {a.red_count} · 黄 {a.yellow_count}
                {' '}<ColorBadge level={a.last_color} size="sm" />
              </span>
            </div>
          ))}
        </div>
      )}
```

- [ ] **Step 2: 加展开 state 并改写走势渲染为折叠多选**

(a) 在 `ProfilePage` 组件内加 state(第 50 行 `const [search, setSearch] = useState('');` 之后):

```tsx
  const [expanded, setExpanded] = useState<number[]>([]);
  const toggleExpand = (i: number) =>
    setExpanded(prev => prev.includes(i) ? prev.filter(x => x !== i) : [...prev, i]);
```

(b) 把 `topTrends.map((t, i) => { ... })` 的整段函数体(约第 150-177 行)替换为:

```tsx
          topTrends.map((t, i) => {
            const last = t.points[t.points.length - 1];
            const expandable = t.points.length >= 2;
            const isOpen = expanded.includes(i);
            return (
              <div key={i} style={{
                padding: '10px 0', borderBottom: i !== topTrends.length - 1 ? '1px solid var(--color-border-light)' : 'none',
              }}>
                <div
                  onClick={() => { if (expandable) toggleExpand(i); }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, marginBottom: isOpen ? 4 : 0,
                    cursor: expandable ? 'pointer' : 'default',
                  }}
                >
                  <span style={{ fontSize: 13, fontWeight: 500, flex: 1, minWidth: 0 }}>
                    {t.item_name_standard || t.item_name}
                    {t.trend_direction && (
                      <span style={{
                        marginLeft: 6, fontSize: 11,
                        color: t.trend_direction === 'up' ? 'var(--color-red)' : 'var(--color-green)',
                      }}>
                        {t.trend_direction === 'up' ? '↑' : '↓'}
                      </span>
                    )}
                  </span>
                  <span style={{ color: 'var(--color-text-secondary)', fontSize: 12, whiteSpace: 'nowrap' }}>
                    {last ? `${last.value}${t.unit ? ' ' + t.unit : ''}` : '-'}
                    {last?.color && <ColorBadge level={last.color} size="sm" />}
                  </span>
                  {expandable && (
                    <span style={{ fontSize: 10, color: 'var(--color-text-secondary)' }}>
                      {isOpen ? '▾' : '▸'}
                    </span>
                  )}
                </div>
                {isOpen && <IndicatorTrendChart data={t.points} />}
              </div>
            );
          })
```

> 说明:原来「暂无 > 数据点 → 整行直接不渲染图」的行为保留 —— `expandable=false` 的行点不开、无 chevron;`IndicatorTrendChart` 组件本身对 `data.length < 2` 会返回 null,作为兜底。

- [ ] **Step 3: 构建验证(语法/类型)**

Run: `cd frontend/packages/user-portal && npx tsc --noEmit`
Expected: 无类型错误(`AbnormalDist` 引用已清、`expanded`/`toggleExpand` 无未使用告警)。

> 该包 `package.json` 的 `build` 即 `tsc && vite build`(devDependencies 含 typescript),故在本包目录跑 `npx tsc --noEmit` 是准确的类型校验。若 node_modules 未装齐先 `npm install`。

- [ ] **Step 4: 提交**

```bash
git add frontend/packages/user-portal/src/pages/ProfilePage.tsx
git commit -m "feat: 用户端我的页移除异常分布卡片,走势行默认折叠多选展开"
```

---

### Task 3: 全量回归 + 文档收尾

**Files:**
- 无新增文件;运行验证。

**Interfaces:**
- Consumes: Task 1/2 的代码。

- [ ] **Step 1: 跑 backend 相关全量测试**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/ -q`
Expected: 全部 PASS(service/comparison 全量)。

- [ ] **Step 2: 确认无残留引用**

Run: `cd /data/project/hospitalKnowledgeBase && grep -rn "abnormal_distribution" frontend/packages/user-portal/src/ || true`
Expected: 无输出(前端已不消费;后端保留属预期)。再 grep 后端确认字段仍在:

Run: `grep -n "abnormal_distribution" backend/app/modules/user_profile/service.py`
Expected: 命中(保留字段,勿删)。

- [ ] **Step 3: 提交(如有遗留修改)**

```bash
git status --short && git add -A && git commit -m "chore: overview 改造收尾" 2>/dev/null || echo "无待提交改动"
```

## 验证摘要(手工,发布时执行)

患者端(3001)登录 →「我的健康档案」:
1. 「异常指标分布」卡片不再出现;
2. 走势条目全部收起;点击可展开,可同时展开多条;只有 1 个数值点的条目点不动、无箭头;
3. 上传/存在 >3 次体检的用户,走势最多显示最近 3 次;改 `backend/.env` 的 `PROFILE_TREND_REPORT_LIMIT=5` 并重启 backend 后显示最近 5 次。
