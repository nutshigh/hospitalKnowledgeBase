# 用户健康档案 + 上传对比功能设计

**Date:** 2026-07-12
**Status:** Pending Approval
**Scope:** 用户门户 (user-portal) 个人健康档案页 + 新报告上传后的对比卡片

## 背景与动机

体检者通常会按年度或多年度上传多份体检报告。当前 user-portal 只有:

- `HomePage` 一页平铺所有报告卡片,无聚合视图
- `ProfilePage` 仅为设置菜单(消息通知/设置/退出登录)
- `ReportDetailPage` 只能看本次报告自己的指标和解读,无法回看与历史报告的变化
- `ReportIndicator` 已有 `item_name_standard` 标准化字段,跨报告指标已可对齐,但目前无人利用
- `TrendMiniChart` 组件存在但未在用户端使用(只用于 statistics)

用户上传第二份及以上报告后,无法直观看到与之前报告的对比关系,缺乏"健康变化画像"。

## 目标

1. **档案页**:在 `/profile` 路由(已存在于底部 tab)重写为「我的健康档案」页,展示该用户历史所有报告的聚合视图 —— 顶部摘要、异常指标分布、可量化指标的走势图。
2. **上传对比卡**:在 `ReportDetailPage` 嵌入 `ComparisonCard`,展示当前报告与上一份报告(按 `report_date`)的指标差异、总体红黄区数量变化、AI 生成的小段健康变化小结。
3. **不破坏现状**:不新建业务表,只在 `report_interpretation` 加两列缓存 AI 小结;不改动 `TrendMiniChart`(只服务 statistics 模块)和 doctor-portal/admin-portal。

## 非目标(Out of Scope)

- 不在档案页支持手动「关注指标」的功能
- 不在 upload 流程中插入额外跳转 —— 对比信息在报告详情页里就能看到
- 不为 doctor-portal / admin-portal 同步开发对比视图
- 不构建独立的「健康分」或综合评分类产品,只做白描式趋势与对比
- 不实现指标参考范围的跨年人群修正(只做原始值走势)

## 现有数据可复用资产

| 资产 | 位置 | 用途 |
|------|------|------|
| `ReportInfo` | `report/models.py:24` | 跨报告按 `user_id` + `report_date` 聚合 |
| `ReportIndicator.item_name_standard` | `report/models.py:45` | 跨报告同指标对齐的关键字段 |
| `ReportInterpretation.red_count/yellow_count/green_count` | `interpretation/models.py:11` | 总体红黄区数量走势 |
| `IndicatorJudgment.color_level` | `interpretation/models.py:32` | 指标单次颜色走判,走势点染色 |
| `term_normalizer` | `app/core/term_normalizer.py` | 兜底标准化(上传时已运行,存到 `item_name_standard`) |
| `get_chat_model` | `app/ai/llm.py` | 调 MedGo 生成对比小结 |
| `strip_think_tags` | `app/ai/agents/think_filter.py` | 清洗对比小结中的 think 标签 |
| `TrendMiniChart.tsx` | 用户端组件 | **不动**(怕影响 statistics 模块的消费方);另建 `IndicatorTrendChart.tsx` |
| `ColorBadge.tsx` | 用户端组件 | 复用,展示总体级别和指标颜色 |
| 多租户中间件 `hospital_context` | `app/middleware/` | 走 hospital 路由 DB 隔离 |

## 架构改动概览

```
新增模块: backend/app/modules/user_profile/
   ├── router.py        3 个接口
   ├── service.py       overview/compare/ai_summary/try_generate_comparison_summary
   ├── comparison.py    纯函数:指标匹配、delta 计算、status 判定、prompt 构造
   └── schemas.py       (可选)

新增字段: report_interpretation 加两列
   ├── comparison_summary TEXT NULL
   └── comparison_baseline_id BIGINT NULL

改造钩子: interpretation/worker.py 解读完成时调
   try_generate_comparison_summary(db, report_id)  # lazy import 防循环

新增前端组件:
   ├── IndicatorTrendChart.tsx   数值型折线图
   └── ComparisonCard.tsx        报告对比卡
重写: ProfilePage.tsx
```

## 后端接口设计

新模块 `app/modules/user_profile/`,在 `main.py` 挂载路由 `prefix="/profile"`,依赖项走完整 `hospital_context` 路径(同 `report/router.py:_get_db`)。

### GET `/profile/overview`

**用途**:档案页主数据来源,一次性返回该用户的全部报告聚合数据。无查询参数,基于 `current_user.user_id` 过滤。

**响应结构**:

```json
{
  "user_summary": {
    "total_reports": 8,
    "earliest_date": "2023-04-10",
    "latest_date": "2026-06-15",
    "latest_overall_level": "yellow",
    "latest_red": 3, "latest_yellow": 5, "latest_green": 12,
    "baseline_date": "2025-11-02"
  },
  "indicator_trends": [
    {
      "item_name_standard": "空腹血糖",
      "item_name": "血糖",
      "unit": "mmol/L",
      "points": [
        {"report_id": 1, "report_date": "2023-04-10", "value": 5.4, "color": "green"},
        {"report_id": 7, "report_date": "2025-11-02", "value": 7.2, "color": "red"},
        {"report_id": 9, "report_date": "2026-06-15", "value": 6.8, "color": "red"}
      ],
      "latest_deviation": "red",
      "trend_direction": "up"
    }
  ],
  "abnormal_distribution": [
    {"item_name_standard": "空腹血糖", "red_count": 2, "yellow_count": 1, "last_color": "red"}
  ]
}
```

**SQL 逻辑**:

1. `report_indicator` JOIN `report_info` on `report_id`,filter `user_id = :uid`,order by `report_date ASC`
2. 在 Python 端过滤 `result_value` 可转 float 的行(数值型指标)
3. 按 `item_name_standard`(空则回退到 `item_name`)聚,组内按 `report_date` 排序组装 `points`
4. `color` 从 `indicator_judgment` left join 同 `indicator_id` 获取(若已解读),否则 null
5. `trend_direction`:`points[-1].value - points[-2].value` 正负,1 个点时 null
6. `abnormal_distribution`:`GROUP BY item_name_standard` 统计 red_count / yellow_count(取所有出现异常的指标),按 `red_count DESC, yellow_count DESC` LIMIT 20

**指标排序**:`indicator_trends` 返回时按 (最近一次 color 为 red/yellow 优先) + (波动幅度 = max-min) 综合 desc 排,前端默认展示 top 10,其余可搜索。

### GET `/profile/compare?report_id=X&baseline_id=Y`

**用途**:取得当前报告与基准报告的指标对比明细。`baseline_id` 缺省时后端按 `report_date < current.report_date ORDER BY report_date DESC LIMIT 1` 自动选,fallback `created_at` 排序。

**响应结构**:

```json
{
  "current": {
    "report_id": 9, "report_date": "2026-06-15",
    "overall_level": "yellow",
    "red_count": 3, "yellow_count": 5, "green_count": 12
  },
  "baseline": {
    "report_id": 7, "report_date": "2025-11-02",
    "overall_level": "red",
    "red_count": 5, "yellow_count": 3, "green_count": 10
  },
  "delta_summary": {"red_delta": -2, "yellow_delta": 2, "green_delta": 2},
  "indicators": [
    {
      "item_name_standard": "空腹血糖",
      "item_name": "血糖",
      "current_value": "6.8", "baseline_value": "7.2",
      "unit": "mmol/L",
      "current_color": "red", "baseline_color": "red",
      "delta": -0.4, "delta_pct": -5.6,
      "status": "improved"
    }
  ],
  "only_in_current": [{"item_name": "尿酸", "current_value": "420", "unit": "μmol/L"}],
  "only_in_baseline": [{"item_name": "丙氨酸氨基转移酶", "baseline_value": "35", "unit": "U/L"}],
  "ai_summary": "对比 2025-11-02 的报告,您本次血糖从 7.2 降至 6.8...",
  "ai_summary_cached": true
}
```

### GET `/profile/ai-summary?report_id=X&baseline_id=Y`

**用途**:前端切换基准时单独刷新 AI 小结,避免重算指标差异。

**响应**:`{"ai_summary": "...", "cached": false}`

读取规则:
- 若 `report_interpretation.comparison_summary` 非空 **且** `comparison_baseline_id == 请求的 baseline_id` → 返回 cached=true
- 否则实时调 LLM 生成并返回 cached=false,**不写回缓存**(只有 worker 那次自动生成的小结才入缓存,前端切换基准生成的临时小结不入缓存,避免缓存错乱)

## AI 小结生成方案

### 触发时机

挂在 `interpretation/worker.py` 的解读完成钩子上。读取完成时:
- `IndicatorJudgment` 已全部生成,可摘取关键异常信息
- LLM 已在跑,追加一次调用边际成本低
- 失败静默不影响主流程(try/except + logger.warning)

### Benchline 选择

复用「自动基准」逻辑:`report_date < this.report_date ORDER BY report_date DESC LIMIT 1`。若用户历史不足 2 份,跳过 AI 小结生成。

### Prompt 设计

不透传原始报告(避免 token 爆炸),先在 Python 里把结构化对比数据压缩成文本表:

```
你是体检报告解读助手。基于下方两份报告的对比数据,用通俗易懂的中文写一段健康变化小结(150-250 字)。

## 本次报告(2026-06-15)
- 总体:黄区 | 红区3 黄区5 绿区12
- 异常指标:
  - 空腹血糖:6.8 mmol/L(红区,上次7.2,↓0.4)
  - 收缩压:145 mmHg(红区,上次130,↑15)
  - 甘油三酯:2.1 mmol/L(黄区,上次1.9,↑0.2)

## 上一份报告(2025-11-02)
- 总体:红区 | 红区5 黄区3 绿区10

## 小结要求
1. 先说整体变化(红黄区数量变化、新增/消失的异常)
2. 再点出明显改善和明显恶化的指标
3. 给出 1-2 条针对性建议(基于上述指标,不编造)
4. 不下诊断,语气同解读模块
5. 不输出 thinking 标签
```

调用 `get_chat_model(streaming=False, max_tokens=512)`,结果 `strip_think_tags` 后写回 `report_interpretation.comparison_summary` 与 `comparison_baseline_id`。

### 摘录指标筛选

异常摘录只取 top 5:红区优先,其次黄区,同色相比 |delta| 降序。避免 prompt 过长。

## 数据模型变更

### DDL

```sql
ALTER TABLE report_interpretation
  ADD COLUMN comparison_summary TEXT NULL AFTER summary_refs,
  ADD COLUMN comparison_baseline_id BIGINT NULL AFTER comparison_summary;
```

两列都 nullable,存量行默认 NULL,不影响现网。

### Model 变更

`backend/app/modules/interpretation/models.py` 中 `ReportInterpretation` 追加:

```python
comparison_summary = Column(Text, nullable=True)
comparison_baseline_id = Column(BigInteger, nullable=True)
```

## Worker 改造

`interpretation/worker.py` 主流程解读完成、`db.commit()` 落库 `ReportInterpretation` 后:

```python
try:
    from app.modules.user_profile.service import try_generate_comparison_summary
    try_generate_comparison_summary(db, report.id)
except Exception as e:
    logger.warning("comparison summary generation failed: %s", e)
```

`try_generate_comparison_summary` 内部:
1. 找该用户的上一份报告(`report_date < current ORDER BY report_date DESC LIMIT 1`)
2. 计算 `delta_summary` + 异常指标 top 5 摘录
3. 调 MedGo 生成小结,`strip_think_tags` 后写回 `comparison_summary` + `comparison_baseline_id`

## 前端改造

### ProfilePage.tsx 重写

`/profile` 路由保留(已在 Layout tab 和 router 中),页面内容重写:

```
┌─────────────────────────────────┐
│ Header: "我的健康档案"            │
├─────────────────────────────────┤
│ 顶部摘要卡                        │
│  ├ 总报告数 · 体检跨度             │
│  ├ 最近一次总体级别 ColorBadge     │
│  └ 红/黄/绿区计数三色横条           │
├─────────────────────────────────┤
│ 异常指标分布                       │
│  └ 每项一行:                      │
│     指标名 · 红N次/黄N次 · 最近色  │
├─────────────────────────────────┤
│ 指标走势(搜索框 + 默认 top 5)     │
│  └ 每个 item 一张卡:              │
│     名称 · 单位 · 当前值 · ColorBadge│
│     IndicatorTrendChart            │
├─────────────────────────────────┤
│ 底部「设置」入口                   │
│  └ 退出登录                       │
└─────────────────────────────────┘
```

「消息通知」「设置」原菜单降级为底部小入口,不抢主位。

### 新建 ComparisonCard.tsx

嵌入 `ReportDetailPage.tsx`,放在红黄区计数条 与 指标列表之间。仅当存在历史报告且当前已解读完成时渲染:

```
┌─────────────────────────────────┐
│ 📊 与上次报告对比                  │
│  └ 右侧:Select 切换基准报告        │
├─────────────────────────────────┤
│ 总体变化:                         │
│  红区 5→3 (↓2)  黄 3→5 (↑2)      │
├─────────────────────────────────┤
│ 指标差异表(默认 6 项,展开看全部):   │
│  指标名   上次 → 本次   差值 状态    │
├─────────────────────────────────┤
│ AI 小结(可折叠):                  │
│  "对比 2025-11-02 的报告..."       │
└─────────────────────────────────┘
```

**基准切换交互**:用户在 Select 切换基准时,前端只调 `GET /profile/ai-summary?report_id=X&baseline_id=Y` 刷新小结段,指标差异前端本地重算(数据已缓存在 state 中,不重新请求)。

### 新建 IndicatorTrendChart.tsx

为档案页数值型指标走势设计,**不动 `TrendMiniChart.tsx`**(后者服务 statistics 模块,改动有回归风险)。

- 入参:`{ data: { report_id: number; report_date: string; value: number; color?: string }[] }`
- 轻量 SVG polyline,横轴短日期(MM-YY),纵轴自适应 min/max
- 点位颜色按 `color` 染色(red/yellow),默认 green 系灰点
- `< 2` 个点时返回 null,只显示当前数值 + ColorBadge

### 改造 ReportDetailPage.tsx

在现有 `interpretation.status === "completed"` 检查通过后,额外调一次 `GET /profile/compare?report_id=X`,把数据传给 `ComparisonCard`。若返回 404/无基准(用户历史报告不足 2 份),不渲染卡片(静默)。

## 错误处理

### 后端 service 层

| 错误场景 | 处理方式 |
|---------|---------|
| 用户历史报告数 < 2 | `ai_summary = ""` 不报警告,前端按"无对比数据"占位 |
| `result_value` 非数值 | 跳过该指标 delta,归入 `only_in_*` 列表 |
| `item_name_standard` 双边都空 | fallback `item_name` 原名匹配 |
| 基准 `report_id` 不存在或非本人 | 返回 404 |
| `baseline_id` 非该 user 历史报告 | 返回 400 ValidationException |
| LLM 调用失败 | 返回空 `ai_summary`,记 logger.warning |

### Worker 钩子

- 整段 try/except,失败仅 `logger.warning`,不重试、不阻塞主流程
- 缓存命中(`comparison_summary` 且 `comparison_baseline_id` 一致)时跳过 LLM 调用

### 前端

| 场景 | UX |
|------|-----|
| `/profile/overview` 4xx/5xx | 整个档案页显示空状态:"暂无档案数据" |
| `/profile/compare` 4xx/5xx | 不渲染对比卡区域(不报红) |
| `ai_summary` 为空 | 显示"AI 小结暂不可用,查看下方指标对比详情" |
| 基准切换请求在途 | Summary 段显示 `<Spin />` 占位 |
| 趋势数据 < 2 个点 | `IndicatorTrendChart` 返回 null |

## 测试策略

### 纯函数单测(`backend/tests/user_profile/`)

无 DB 依赖,直接测 `comparison.py` 纯函数,沿用现有 `tests/ai/agents/test_chat_planner.py` 的 pytest + mock 风格:

| 测试点 | 期望 |
|-------|------|
| 指标按 `item_name_standard` 匹配 | 同 standard 不同原名 → 匹配 |
| `item_name_standard` 空 fallback `item_name` | 同名匹配 |
| 数值型 delta 计算 | "7.2" - "6.8" = -0.4,单位一致才减 |
| `status` 阈值判定 | delta_pct ≥ +5% → worsened, ≤ -5% → improved, 其间 stable |
| 非数值 result_value | "阳性" / "++" → 跳过 delta |
| 单边出现的指标 | 归 `only_in_current` / `only_in_baseline` |
| `trend_direction` | `points[-1].value - points[-2].value` 正负 |

### Worker 钩子集成测试

Mock LLM(避免依赖 vLLM 服务在线):
- 用 `monkeypatch` 把 `get_chat_model()` 替换为 fake,验证字段写入正确
- 验证异常路径(LLM 抛错)不会冒泡到主流程,只记 warning

### 前端

无单测框架接入(前端 `package.json` 只有 `dev` 与 `build` 脚本)。验证方式:
- `npm run build`(包含 `tsc && vite build`)→ 确保 TS 类型与构建通过
- 手测:iPhone 视口 + Desktop 1366 视口,确认对比卡与趋势卡布局正确
- 手测:走完整 start.sh → 上传第二份报告 → 等待 worker 解析完成 → 进报告详情页看对比卡 + 档案页看趋势

## 文件清单

### 后端新增

```
backend/app/modules/user_profile/
├── __init__.py
├── router.py
├── service.py
├── comparison.py
└── schemas.py

backend/tests/user_profile/
├── __init__.py
└── test_comparison.py
```

### 后端改造

- `interpretation/models.py` — `ReportInterpretation` 加 2 字段
- `interpretation/worker.py` — 解读完成钩子调 `try_generate_comparison_summary`
- `main.py` — 注册 `user_profile.router`
- 一份独立 migration SQL(`ALTER TABLE ... ADD COLUMN ...`)

### 前端新增

```
frontend/packages/user-portal/src/components/
├── ComparisonCard.tsx
└── IndicatorTrendChart.tsx
```

### 前端改造

- `pages/ProfilePage.tsx` — 重写为健康档案页
- `pages/ReportDetailPage.tsx` — 在解读完成后插入 `ComparisonCard`