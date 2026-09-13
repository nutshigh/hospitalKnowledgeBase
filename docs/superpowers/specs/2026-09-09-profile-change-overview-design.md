# 用户端「我的」跨报告健康变化总览(挪移报告对比 + 自动最近 N 份)

日期:2026-09-09

## 背景与目标

用户端(patient portal 3001)报告详情页 `/report/:id` 的「📊 与历史报告对比」卡片
(现 `ComparisonCard`,双报告 + 手动基线下拉 + 逐指标差异表)存在两个问题:

1. 位置在报告详情页,用户要逐份报告点进去才能看到「和过往报告的对比」,缺少入口级的整体健康走向视图。
2. 需要手动选基线;产品希望**去掉选择、自动对比最近若干份已完成解读的报告**,给出一份
   **总体性变化概述:结论 / 建议 / 注意事项**。

本次改造:

- 将该能力**挪移**到「我的」tab(`/profile` 页),报告详情页移除对比卡片。
- 去掉基线下拉,**自动**取该用户最近 ≤3 份已完成 AI 解读的报告对比。
- 卡片内容 = **AI 总览(结构化三节)** + **少量关键指标变化**(符合用户确认的"AI总览 + 少量关键指标")。
- 后端改造时**考虑该能力后续迁往另一个外部 App**:新端点做成单次自包含的规范 GET,
  沿用现有外部访问标准(`app-login` + Bearer role='user' + 双锚定),前端仅为渲染层。

### 已确认口径

- 「最近 N 份」按 `report_date` 取(用户已确认);N 复用现有配置
  `settings.PROFILE_TREND_REPORT_LIMIT`(默认 3),与「指标走势」同口径,不新造常量。
- 只统计**已完成 AI 解读**(`report_interpretation.status == 'completed'`,有红黄绿计数)的报告;
  未解读/解读失败/仅 processing 占位行不计入窗口。
- 有 2 份用 2 份、有 3 份用 3 份;**不足 2 份**时不生成,返回空降级由前端展示引导文案。
- 旧对比卡片从报告详情页移除(用户已确认),相关旧后端代码一并退役(挪动语义)。

## 外部接入标准对齐(重要)

- 新端点挂在 `/api/v1/profile/change-overview`,鉴权沿用 `get_current_user` + `user_identity`
  双锚定,不设额外 role 限制 → 与现有 `/reports/*`、`/chat/*` 同一信任模型:外部 App
  持 `APP_API_KEY` 经 `POST /api/v1/auth/app-login`(`name + id_card_suffix`)换取
  role='user' 的 Bearer JWT 后直接可调;`X-Hospital-Id` 对 role='user' 一律忽略(不跨院)。
- 响应**一次给全、可独立渲染**:`reports` + `key_indicators` + `summary` 全字段,
  不依赖前端私有状态或二次轮询;报告按锚定过滤,不含医生侧/跨院数据。
- 返回契约字段名/类型固定(数值字符串原样透传、`report_date` 与现有接口同格式、
  `summary` 四键),便于未来 App 与当前 portal 共用同一 schema 渲染。

## 后端改动(`backend/app`)

### 1. Router(`app/modules/user_profile/router.py`)

- **删除**:`GET /api/v1/profile/compare`、`GET /api/v1/profile/ai-summary`(唯一消费者
  `ComparisonCard` 已移除,无其它调用方,见影响面核对)。
- **新增**:

```python
@router.get("/change-overview")
def change_overview(db=Depends(_get_db), current_user=Depends(get_current_user)):
    uid, nm = user_identity(current_user)
    if uid is None:
        return {"reports": [], "covered": 0, "reason": "insufficient", ...降级}
    return service.get_change_overview(db, uid, nm)
```

降级响应固定结构(前端据此渲染引导,不再 400):

```jsonc
{ "reports": [], "covered": 1|0, "reason": "insufficient",
  "key_indicators": [], "summary": null, "cached": false }
```

### 2. 取窗 `service._change_window(db, user_id, name)`

- 查锚定 `ReportInfo`,**仅保留存在 `ReportInterpretation.status == 'completed'` 的报告**,
  按 `report_date` 升序排序(与 `/overview` 一致:MySQL ASC 下 NULL 日期垫最旧;
  有日期报告不足 N 份时才计入无日期报告)。
- 取最近 `min(len, settings.PROFILE_TREND_REPORT_LIMIT)` 份;不足 2 份返回空窗。
- 每份带其 `ReportInterpretation`(取 overall_level / 红黄绿计数 / interp.id)。

### 3. 服务层 `service.py` 核心函数

#### `build_change_overview(db, user_id, name)`(纯计算 + 读缓存)
1. 算窗口;窗口 < 2 → 返回降级 dict。
2. 读「窗口最新报告」的 `interp.comparison_summary`,若为 JSON 且内部签名与当前窗口一致
   → 直接反序列化整份响应返回 `cached=true`(**命中路径与重算路径返回结构完全相同**)。
3. 否则组装整份响应(报告头含日期/总体等级/红黄绿计数,见 §4 关键指标,见 §5 调 LLM 生成
   `summary`);成功则把 **`{signature, payload}` 整体 JSON** 写回该最新 interp 的
   `comparison_summary` 并 `db.commit()`,返回 `cached=false`;LLM 失败/JSON 解析失败 →
   不写缓存,返回 `summary: null` + 已算好的报告头与 `key_indicators`。

缓存 JSON 结构(列内为整份响应,命中即可原样返回):

```jsonc
{ "signature": [ {"report_id": 1, "interp_id": 11}, ... ],   // 窗口内逐份,顺序与 payload.reports 一致
  "payload": {
    "reports": [ {"report_id","report_date","overall_level","red_count","yellow_count","green_count"}, ... ],
    "covered": 3, "key_indicators": [...], "summary": {...}
  }
}
```

#### `ensure_change_overview(db, report_id)`(worker 钩子,替代 `try_generate_comparison_summary`)
- 由刚解读完成的 report 取锚定 → `build_change_overview`(读缓存逻辑已含,不足 2 份自动跳过;
  成功即写回缓存 = 后台预热)。任何异常吞掉仅 `logger.warning`,不阻塞解读 worker 收尾。

### 4. 关键指标 `_key_indicators(db, window)`

复用现有对齐思路(把 `comparison.py` 的双报告纯函数泛化成 N 报告;见 §6):
- 取窗口内全部 `ReportIndicator`,数值可解析的按 `item_name_standard or item_name` 聚合为
  `points:[{report_id, report_date, value(float), color}]`,points 按 report_date 升序。
- **候选 = 出现在 ≥2 份窗口报告** 的指标,且满足任一:
  (a) 窗口内任一点 color ∈ {red, yellow};
  (b) 首尾两点均有数值且 `|delta_pct| ≥ 5`(有变化)。
- 排序:该指标窗口内**最近一次异常点颜色**(红 > 黄 > 无)优先;同级按窗口内最大 `|delta_pct|`
  降序;再同级按指标名。取前 8 个喂 prompt、前 5 个进响应 `key_indicators`。
- 每项输出:`item_name`、`unit`、`latest_value`(字符串原样)、`latest_color`、`direction`
  (`up/down/None`,复用 `trend_direction`)、`delta_pct`(首尾两点)、`points`。

### 5. AI 生成与 prompt

- 单轮 ChatOpenAI 直调 MedGo:复用 `get_chat_model(streaming=False)`、并发闸 `_guarded`、
  `strip_think_tags`(service.py 现有模式),`max_tokens` 适量放宽(≤1024),低温。
- prompt(`comparison.py::build_change_prompt` 取代 `build_comparison_prompt`)输入仅结构化文本:
  - 每份报告一行:`{日期} 总体{等级},红/黄/绿 {计数}`(3 行,按日期升序);
  - 关键指标行:`- {指标名}: 各份值(单位)[颜色],首尾 ↑/↓delta`,最多 8 个;
  - 明确要求:输出**纯 JSON 对象**(禁 markdown fence/think 标签),键严格为
    `trend_summary / conclusion / suggestions / precautions`,中文字符串;
    `trend_summary` 描述窗口内整体走向(<=80 字),`conclusion` 提炼关键变化(<=120 字),
    `suggestions` 给 1-3 条针对可量化指标(如血糖/血脂)的健康建议(<=150 字),
    `precautions` 给复查/就医注意事项(<=100 字);**不下诊断、不给绝对数值**、
    变化要落到该窗口实际异常/变动指标上。
- 解析兜底:先 `json.loads`;失败则剥 fence + 正则取首个 `{...}` 再试;仍失败 → `None`(不缓存)。

### 6. `comparison.py` 收敛为 N 报告纯工具

- `match_indicators(current, baseline)` → `align_indicators(rows_by_report)`(N 份对齐,按
  standard 优先、无 standard 时同名配对,配对规则语义不变)。
- `compute_delta` / `judge_status` / `trend_direction` 保留。
- `build_comparison_prompt` → `build_change_prompt`(见 §5);旧函数删除。
- 旧 service 双报告函数 `get_comparison` / `get_ai_summary` / `_build_indicator_diff` /
  `_auto_select_baseline` 的**双报告专用**部分删除。**保留** `_auto_select_baseline` 本体
  (仍被 `get_overview` 用来算 `user_summary.baseline_date`,见 service.py:166-168)。

### 7. Schema / 缓存列(零 DDL)

- **不加列、不动表**。复用「窗口最新报告的 `report_interpretation`」的既有列
  `comparison_summary`(TEXT)存整份 JSON(`comparison_baseline_id` 弃用置空)。
- 存量旧数据自愈:旧列里是历史双报告纯文本(非 JSON)或签名不匹配 → 解析失败即视为失效,
  重新生成覆盖。重解读(interp `persist` 删旧行建新行)→ interp_id 变 → 签名失效自动重算。
- 并发:缓存失效时可能多个请求同时生成,接受小概率重复调用(幂等覆盖),不做分布式锁。

### 8. Worker 接线

`app/modules/interpretation/worker.py`(interpretation 成功后、`try_generate_followup` 之后)
把 `try_generate_comparison_summary` 调用替换为 `ensure_change_overview`;失败吞异常不阻塞。

## 前端改动(`frontend/packages/user-portal`)

### 1. 报告详情页移除对比卡片

- `src/pages/ReportDetailPage.tsx:272-274` 删 `<ComparisonCard reportId={Number(id)} />`,
  import 一并删除。
- 删除 `src/components/ComparisonCard.tsx`。

### 2. 「我的」页新增卡片(`src/pages/ProfilePage.tsx`)

- 进页面与 `/profile/overview` **并行**请求 `GET /api/v1/profile/change-overview`。
- 位置:卡片 1(头像/汇总)与「指标走势」之间,新增标题 **「📈 近期健康变化」**(无选择器)。
- 响应 `covered >= 2`:
  - 头部一行说明:`已自动对比最近 {covered} 份已完成解读的报告({首份日期} ~ {末份日期})`。
  - **AI 区块**四小节:变化概述(引导段)→ 结论 → 建议 → 注意事项;未命中缓存、同步生成中显示骨架/加载态。
  - `summary === null`(LLM 失败/解析失败)时 AI 区块显示兜底文案(「AI 总览暂不可用,请查看下方关键指标」),
    关键指标照常展示。
  - **关键指标变化**区:`key_indicators` 前 5 条,每条 = 指标名 + 最新值/单位 + `ColorBadge`
    (最新色)+ ↑↓ 方向 + `delta_pct`;多余可「展开全部」。
  - 页脚静态免责:`本内容由 AI 依据指标数值自动生成,仅供参考,不构成医疗诊断;指标异常请遵医嘱复查。`
- `covered < 2`(≥1 份):卡片区显示引导文案
  「完成 ≥2 份报告的 AI 解读后,这里将自动生成跨报告健康变化分析」;无报告时保持页面既有空态。
- 复用现有 `api`(zustand)、CSS 变量、`ColorBadge`;不引入新 UI 依赖、不做前端单测(项目无该基建),`npx tsc --noEmit` 验证。

## 测试(`backend/tests`)

重写/新增于 `tests/user_profile/test_service.py`,沿用现有 `db` fixture 与 LLM mock 模式:
1. 取窗:5 份已完成解读 + 1 份未解读(processing/无行)→ 取最近 3 份已完成;不足 3 → 取 2 份。
2. 无日期报告垫最旧,有日期充足时不入窗;`PROFILE_TREND_REPORT_LIMIT` 生效。
3. 缓存命中:签名一致 → 不调 LLM、`cached=true`、数据一致。
4. 缓存失效自愈:列存旧纯文本 / interp_id 变化 → 重新生成并覆盖,`cached=false`。
5. LLM 返回非法 JSON(带 fence)→ 解析兜底成功;彻底失败 → `summary=None` 但 `key_indicators` 非空、不写缓存。
6. worker 钩子 `ensure_change_overview`:≥2 份写缓存;仅 1 份跳过不写;LLM 异常被吞不抛。
7. 关键指标:仅单份出现的指标不进候选;红/黄优先于纯 |delta|≥5;排序断言。

同步更新既有引用:
- `tests/followup/test_worker_hook.py:45`、`tests/test_interp_worker_bulk.py:54,198` patch 目标改为
  `ensure_change_overview`。
- `tests/user_profile/test_service.py` 原双报告用例(get_comparison / get_ai_summary /
  try_generate_comparison_summary)删除或改写成新函数用例。

## 明确不做 / 保留

- 不做「重新分析」按钮(窗口变化时自动重算;首次冷缓存由 worker 预热 + GET 兜底生成)。
- `_auto_select_baseline` 保留给 `/profile/overview` 的 `baseline_date`;`abnormal_distribution`
  后端字段保留(2026-09-08 spec 决定,前端已不消费,不回退)。
- doctor-portal / statistics 不依赖 `/profile/*`,零改动。
- 不做前端单测(项目无基建),tsc 验证。

## 影响面核对

- 依赖方:`ComparisonCard` 唯一消费者为报告详情页(本次移除);`/profile/compare`、`/ai-summary`
  与 `try_generate_comparison_summary` 无 doctor/admin 或 statistics 依赖(已 grep 核对)。
- 零 schema 迁移:不新增表/列;仅复用 `report_interpretation.comparison_summary` 列语义。
- 新端点不改变 `/profile/overview`、`/reports/*`、`/interpretations/*` 契约。
- 外部 App 视角:`GET /api/v1/profile/change-overview` 为新增只读端点,`app-login` role='user'
  可直接调用,无破坏性变更。

## 验证

```bash
cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py tests/followup/test_worker_hook.py tests/test_interp_worker_bulk.py -q
cd frontend/packages/user-portal && npx tsc --noEmit
```

手工(患者端 3001):
1. 报告详情页确认「与历史报告对比」卡片消失,AI 解读报告卡片仍在。
2. 「我的」页出现「📈 近期健康变化」卡片:有 ≥2 份已完成解读报告时自动展示三节 AI 总览 +
   关键指标;报告不足 2 份显示引导文案;首次(无缓存)出现加载态后落缓存,再次进入秒开。
3. 用 app-login token 直接 `curl /api/v1/profile/change-overview` 返回同一自包含结构。

## AGENTS.md 跟进(可选)

本特性退役了 `/profile/compare`、`/ai-summary` 与 `try_generate_comparison_summary`,
并把 `report_interpretation.comparison_summary` 列语义改为「跨报告健康变化总览 JSON」。
若后续维护需要,建议在 AGENTS.md 补一行记录(涉及面小,不阻塞本实现)。
