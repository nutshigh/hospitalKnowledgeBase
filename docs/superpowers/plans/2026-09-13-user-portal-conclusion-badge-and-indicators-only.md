# 用户端报告详情：结论栏色标折叠 + 指标列表去总检异常 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户端报告详情页的"总检建议与结论"面板加上"默认黄/命中红"色标并收起时不显示内容；指标列表只保留真指标，计数条同步只统计真指标。

**Architecture:** 纯前端，只改 `user-portal` 的单个页面组件。复用已有 `ColorBadge` 与 `countLevels`；不改后端/API、不改 doctor-portal。

**Tech Stack:** React 18 + TypeScript + Vite + antd（user-portal `:3001`）。

## Global Constraints

- 只修改 `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`；不改后端/API，不改 doctor-portal/admin-portal，不改 `@hospital/shared`。
- 结论栏色标口径：`interpretation.overall_level === 'red'` → `red`，否则一律 `yellow`（含无 interpretation 的情况）。
- 指标列表只保留 `source !== 'conclusion'` 的真指标；计数条用过滤后的真指标前端重算。
- 本仓库约定：**未经用户明确同意不得 `git commit`**。下方 Commit 步骤仅在用户同意后执行。
- 本组件无自动化测试框架（package.json 无 test 脚本）；验证 = `tsc` 类型检查 + `vite build` + 人工浏览器验收。
- 不新增代码注释（保持最小 diff）；仅做必要代码改动。
- 命令一律在仓库根 `/data/project/hospitalKnowledgeBase` 下执行。

---

### Task 1: 指标列表只保留真指标 + 计数条重算

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`

**Interfaces:**
- Consumes: 现有 `rawIndicators`、`isConclusionIndicator`、`countLevels`、`toGroups`、`COLOR_ORDER`、`displayName`。
- Produces: `regularIndicators: any[]`（真指标）、`displayIndicators: any[]`、`levelCounts: { red: number; yellow: number; green: number }`（供 Task 2 之后的计数条使用）。

- [ ] **Step 1: 用"只留真指标"替换结论去重与合并逻辑**

在 `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx` 中，将下列整段：

```tsx
  // 分离结论型指标和化验型指标，结论型优先剔除与化验型同名的
  // 2026-08-31: 名称变体也视为重复(互相包含, 如结论"肌酸激酶" vs 指标"血肌酸激酶")
  // 2026-09-01: 只对"异常指标"(黄/红)去重 —— 绿色指标("甲状腺"/"呼吸"/"钙"
  // 等短名、"右侧耳前瘘管鼻"检查项)不拦截结论条目; 包含匹配要求双方≥4字
  // (防短名"甲状腺"⊂"甲状腺结节"误滤)。
  const conclusionIndicators = rawIndicators.filter(isConclusionIndicator);
  const regularIndicators = rawIndicators.filter((ind: any) => !isConclusionIndicator(ind));
  const regAnomaly = regularIndicators.filter((ind: any) =>
    ind.color_level === 'yellow' || ind.color_level === 'red');
  const regularNames = regAnomaly.map((ind: any) => displayName(ind));
  const isRegularDup = (n: string) => regularNames.some(rn =>
    rn && (rn === n || (rn.length >= 4 && n.length >= 4 && (rn.includes(n) || n.includes(rn)))));
  // 结论条目内部: 短名被更长结论名包含 → 剔除(如"钙化灶" vs "肝内钙化灶")
  const concNames = conclusionIndicators.map((ind: any) => displayName(ind));
  const isConcSub = (n: string) => concNames.some(cn =>
    cn && cn !== n && cn.length >= 4 && n.length >= 4 && cn.includes(n));
  const filteredConclusion = conclusionIndicators.filter((ind: any) => {
    const n = displayName(ind);
    return !isRegularDup(n) && !isConcSub(n);
  });
  const displayIndicators = [...regularIndicators, ...filteredConclusion].sort((a, b) =>
    (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));

  const { groups, flat } = toGroups(displayIndicators, moduleOrder);
  const totalCount = displayIndicators.length;

  const conclusionText = report.conclusion_text ? cleanConclusionText(report.conclusion_text) : '';
```

替换为：

```tsx
  const regularIndicators = rawIndicators.filter((ind: any) => !isConclusionIndicator(ind));
  const displayIndicators = [...regularIndicators].sort((a, b) =>
    (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));

  const { groups, flat } = toGroups(displayIndicators, moduleOrder);
  const levelCounts = countLevels(regularIndicators);

  const conclusionText = report.conclusion_text ? cleanConclusionText(report.conclusion_text) : '';
```

- [ ] **Step 2: 计数条改用 `levelCounts`**

将计数条 JSX 中的三处后端字段改为前端重算值。原：

```tsx
          <span style={{ color: 'var(--color-red)', fontWeight: 600, fontSize: 13 }}>红区 {interpretation.red_count}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-yellow)', fontWeight: 600, fontSize: 13 }}>黄区 {interpretation.yellow_count}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-green)', fontWeight: 600, fontSize: 13 }}>绿区 {interpretation.green_count}</span>
```

改为：

```tsx
          <span style={{ color: 'var(--color-red)', fontWeight: 600, fontSize: 13 }}>红区 {levelCounts.red}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-yellow)', fontWeight: 600, fontSize: 13 }}>黄区 {levelCounts.yellow}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-green)', fontWeight: 600, fontSize: 13 }}>绿区 {levelCounts.green}</span>
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend/packages/user-portal && npx tsc --noEmit`
Expected: 无输出（退出码 0）。

- [ ] **Step 4: 构建**

Run: `cd frontend/packages/user-portal && npm run build`
Expected: `tsc` 通过且 `vite build` 成功（生成 `dist/`），无 error。

- [ ] **Step 5: 人工验收（浏览器，user-portal :3001）**

打开 `http://localhost:3001`，进入 user6（H004）任一报告详情，确认：
- 指标列表里**不再出现**带 📋 / "存在建议" 的结论型条目；分组与平铺区都只有真指标。
- 顶部"红区/黄区/绿区"数字 = 列表中对应颜色的行数（可用分组头 `N项` 与计数核对）。
- 页面其余部分（整体色标、AI 总结卡片、聊天区）正常。

- [ ] **Step 6: 提交（需用户明确同意后执行）**

```bash
git add frontend/packages/user-portal/src/pages/ReportDetailPage.tsx
git commit -m "feat(user-portal): 指标列表只保留真指标, 计数条同步重算"
```

---

### Task 2: 结论栏色标 + 收起态不显示内容

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`

**Interfaces:**
- Consumes: 现有 `overallLevel`、`interpretation`、`conclusionText`、`conclusionExpanded`、`ColorBadge`；Task 1 产出的 `levelCounts` 不受影响。
- Produces: `conclusionBadge: 'red' | 'yellow'`。

- [ ] **Step 1: 新增 `conclusionBadge` 派生值**

在 `const conclusionText = ...` 一行之后，插入：

```tsx
  const conclusionBadge = overallLevel === 'red' ? 'red' : 'yellow';
```

- [ ] **Step 2: 标题行加色标并删除收起态预览**

将下列整段：

```tsx
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
            📋 总检建议与结论
            {conclusionText && !conclusionExpanded && (
              <span style={{ fontWeight: 400, marginLeft: 8, fontSize: 12, color: 'var(--color-text-secondary)' }}>
                {conclusionText.slice(0, 40)}...
              </span>
            )}
          </span>
```

替换为：

```tsx
          <span style={{
            fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            📋 总检建议与结论
            <ColorBadge level={conclusionBadge} size="sm" />
          </span>
```

说明：`ColorBadge` 已在文件顶部导入（`import ColorBadge from '../components/ColorBadge';`）；`conclusionExpanded` 仍用于展开/收起与箭头显隐，展开区显示 `conclusionText || '未提取到结论'` 的逻辑不变。

- [ ] **Step 3: 类型检查**

Run: `cd frontend/packages/user-portal && npx tsc --noEmit`
Expected: 无输出（退出码 0）。

- [ ] **Step 4: 构建**

Run: `cd frontend/packages/user-portal && npm run build`
Expected: 成功，无 error。

- [ ] **Step 5: 人工验收（浏览器，user-portal :3001）**

- 默认收起：结论栏只显示 `📋 总检建议与结论` + 色标，**不显示任何 40 字预览/内容**。
- 点击展开：显示 `report.conclusion_text` 原文（空则"未提取到结论"）；再点击可收起。
- 色标：找一份 `overall_level==='red'` 的报告 → 红标；非红报告（含无解读）→ 黄标。
- 无 conclusion_text 的报告：标题 + 色标仍在，展开显示"未提取到结论"。

- [ ] **Step 6: 提交（需用户明确同意后执行）**

```bash
git add frontend/packages/user-portal/src/pages/ReportDetailPage.tsx
git commit -m "feat(user-portal): 结论栏加黄/红色标, 收起态不显示内容"
```

---

## Self-Review

**Spec coverage:**
- 色标默认黄/红 → Task 2 Step 1-2。
- 收起前不显示内容 → Task 2 Step 2（删除 40 字预览）。
- 指标列表去总检异常、只留指标 → Task 1 Step 1。
- 计数条只统计真指标 → Task 1 Step 1-2。
- 不改后端/doctor-portal → Global Constraints + 仅单文件改动。

**Placeholder scan:** 无 TBD/TODO；每个代码步骤均给完整前后代码。

**Type consistency:** `levelCounts`（Task 1 产出，`{red,yellow,green}`）在 Task 1 Step 2 使用；`conclusionBadge`（Task 2 产出）用于 `ColorBadge` 的 `level`（`string`）。命名跨任务一致。

**已知副作用（spec 已确认）:** 仅由结论型条目判红的报告，计数条显示"红区 0"而结论栏红标；这是用户接受的口径。
