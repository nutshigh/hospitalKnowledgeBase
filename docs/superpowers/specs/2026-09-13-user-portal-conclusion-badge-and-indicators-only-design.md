# 用户端报告详情：结论栏色标折叠 + 指标列表去总检异常

日期：2026-09-13
状态：已与用户确认设计，待写实现计划
范围：仅 `frontend/packages/user-portal`（患者端 :3001），不改后端/API/doctor-portal。

## 背景与问题

用户端报告详情页 `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx` 现状：

- "📋 总检建议与结论"面板：可折叠，但收起态会显示 `conclusion_text.slice(0, 40)` 预览；标题行无颜色分级。
- 指标列表 `displayIndicators = regularIndicators + filteredConclusion`，把结论型条目
  （`source === 'conclusion'`，渲染为 📋 + "存在建议"）与真指标混在一起。
- 顶部"红区/黄区/绿区"计数条直接用后端 `interpretation.red_count/yellow_count/green_count`，
  该计数由"去重后的全部条目（含结论型）"推出。

用户诉求：

1. 结论栏（总结段）要有颜色分级标志：默认黄区，命中红区规则显示红区；展开前不显示
   面板内的任何内容。
2. 前端不再显示"总检异常"（结论型条目），指标列表只显示真指标。

## 已确认的口径（用户逐条拍板）

- 改动范围：**仅 user-portal**。doctor-portal 的"总检建议与结论"卡与"指标明细"表不动。
- 结论栏色标：`interpretation.overall_level === 'red'` → 红标，否则一律黄标（与页面顶部
  整体判定一致；`overall_level` 由后端在含结论型条目的去重结果上计算）。
- 需求 2：指标列表只保留 `source !== 'conclusion'` 的真指标；结论栏保留，收起态只显示
  标题 + 色标、不显示任何内容，展开后显示 `report.conclusion_text` 原文。
- 计数条：改为只统计"真指标"（前端按过滤后的列表重算），与列表一致。
- **接受的已知副作用**：若某报告仅由结论型条目判红，则计数条会显示"红区 0"，但结论栏
  色标为红。这是用户明确选择的口径，不做额外对齐。

## 设计（方案 A：纯前端单文件改动）

改动文件：`frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`。

### 1. 结论栏色标 + 收起无内容

- 新增派生值：
  `const conclusionBadge = interpretation?.overall_level === 'red' ? 'red' : 'yellow';`
- 面板标题行右侧渲染 `ColorBadge`（组件已存在：`../components/ColorBadge`）：
  `<ColorBadge level={conclusionBadge} size="sm" />`。
- 删除收起态的 40 字预览分支（`conclusionText && !conclusionExpanded` 的 `slice(0, 40)` 段落）。
- 交互不变：`conclusionExpanded` 默认 `false`；点击标题行切换；展开后显示
  `conclusionText || '未提取到结论'`。
- 无 `interpretation` 时，色标仍按"默认黄区"渲染黄标。

### 2. 指标列表只保留真指标

- `displayIndicators = regularIndicators`（`rawIndicators.filter(ind => !isConclusionIndicator(ind))`）。
- 移除仅为"结论去重"服务的派生代码：`filteredConclusion`、`regAnomaly`、`regularNames`、
  `isRegularDup`、`concNames`、`isConcSub`。
- `groups` / `flat` 因此不再出现结论型条目。
- 保留 `isConclusionIndicator`（用于过滤）与 `IndicatorRow` 的 `is_conclusion` 能力；
  列表传参恒为 false（不删组件属性）。
- `displayIndicators.length === 0` 的空态仍按"暂无指标数据"渲染。

### 3. 计数条改为只统计真指标

- 用 `countLevels(regularIndicators)` 前端重算，替换 `interpretation.red_count /
  yellow_count / green_count`。
- `countLevels` 已存在，直接复用。
- 仅在 `interpretation` 存在时渲染计数条（维持现状）。

### 4. 不改动

- 后端与 API（`interpretations/{id}` 响应结构不变）。
- doctor-portal（其 `ReportDetailPage` 保持结论卡常开、指标表含结论行 + 原文行）。
- `InterpretationReportCard`（AI 总结卡片）。
- 页面顶部的 `overallLevel` 整体色标、`StatusTag`。

## 边界与错误处理

- `interpretation` 为空：结论栏仍显示标题 + 黄标；展开显示 `report.conclusion_text`
  或"未提取到结论"；指标来自 `report.indicators` 回退（现有逻辑）。
- `conclusion_text` 为空：展开显示"未提取到结论"；收起时不显示任何内容。
- 指标列表为空：显示"暂无指标数据"。
- 结论型条目被隐藏后，`regularIndicators` 为空但报告有结论：列表空态 + 结论栏可展开查看。

## 测试与验证

- 类型检查：`cd frontend/packages/user-portal && npx tsc --noEmit`（无独立测试套件）。
- 构建：`npm run build -w @hospital/user-portal`（`tsc && vite build`）。
- 手动验收：
  - user6（H004，已迁双锚定）任一报告：列表无 📋/"存在建议"条目；结论栏默认收起且
    除标题 + 色标外无文字；展开显示总结段原文。
  - 计数条数字 = 列表中红/黄/绿行数。
  - 找一份 `overall_level==='red'` 的报告：结论栏红标；非红报告：黄标。
  - 结论栏收起→展开→再收起，内容显隐正确。

## 非目标（YAGNI）

- 不抽公共"可折叠结论栏"组件（仅此页使用）。
- 不改 doctor-portal / admin-portal。
- 不改后端 counts 的计算口径。
- 不引入"结论型条目的计数与列表不一致"的额外提示 UI。
