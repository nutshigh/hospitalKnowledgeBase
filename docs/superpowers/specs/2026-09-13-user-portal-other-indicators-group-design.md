# 用户端报告详情：未归类指标收进"其它指标"可折叠组

日期：2026-09-13
状态：设计已确认，待实现
范围：仅 `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`。

## 背景与问题

`toGroups(indicators, moduleOrder)`（`ReportDetailPage.tsx:30-51`）现状：

- 无 `module_order`：全部平铺（`flat`），不可折叠。
- 有 `module_order`：命中命名分组的项进入 `groups`（Collapse 可折叠）；未归类项进入
  `flat`，在折叠区下方以**不可折叠**的平铺列表渲染（`ReportDetailPage.tsx:306-321`）。

需求：把"没有分到类的指标"收进一个"其它指标"分组，使其可展开。

## 已确认口径（用户）

- **仅当已有命名分组时**才把未归类项收进"其它指标"。
- **完全无 `module_order`**（全部未归类、没有任何命名分组）时，保持现有整体平铺不变。

## 设计

只改 `toGroups`：

- 无 `module_order`：原样返回 `{ groups: [], flat: sortByColor(indicators) }`。
- 有 `module_order`：
  1. 已归类项按 `moduleOrder` 顺序成组（现状不变）。
  2. 未归类项进 `flat`。
  3. **若命名分组 `groups` 非空且 `flat` 非空**：向 `groups` 追加
     `{ name: '其它指标', items: sortByColor(flat) }`，并将 `flat` 置空。
  4. 否则保持现状（`flat` 平铺）。
- 渲染逻辑**不改**：`groups` 仍是 Collapse 项，因此"其它指标"自动成为最后一个可折叠项；
  `flat` 分支仅在无 `module_order`（或未命中命名分组）时使用。

## 行为细节

- "其它指标"排在所有命名分组之后（追加在末尾）。
- 组内排序沿用 `sortByColor`（红 > 黄 > 绿）。
- 组头自动显示 `N项` 与红/黄/绿计数（复用现有 label 渲染，`ReportDetailPage.tsx:274-288`）。
- 默认收起（与其它分组一致，`openModules` 初值 `[]`）。
- 组名固定为 `其它指标`；无需处理与真实模块重名（后端模块名中不存在）。

## 边界

- 有 `module_order` 但无任何项命中命名分组 → `groups` 为空 → 不追加"其它指标"，维持平铺。
- 有命名分组但无未归类项 → 不追加"其它指标"。
- 所有指标都归类 → 行为不变。

## 测试与验证

- `cd frontend/packages/user-portal && npx tsc --noEmit`（无测试框架）。
- 浏览器（:3001）user6 报告：未归类的真指标出现在最后一个可折叠的"其它指标"组；
  展开显示条目、组头计数正确；无可折叠退化（即无 module_order 的旧报告仍平铺）。

## 非目标

- 不改后端/API、doctor-portal、admin-portal、`@hospital/shared`。
- 不改 `sortByColor` / `countLevels` / Collapse 渲染结构。
