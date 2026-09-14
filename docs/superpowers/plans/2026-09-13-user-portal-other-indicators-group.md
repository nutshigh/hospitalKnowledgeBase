# 用户端未归类指标收进"其它指标"组 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (single small task).

**Goal:** 有 `module_order` 且存在未归类真指标时，把它们收进末尾可折叠的"其它指标"组；无 `module_order` 时保持平铺。

**Architecture:** 只改 `user-portal` 的 `toGroups` 纯函数，渲染结构不动。

**Tech Stack:** React 18 + TypeScript + Vite（user-portal :3001）。

## Global Constraints

- 只改 `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`；不改后端/doctor-portal/shared。
- 仅"已有命名分组且存在未归类项"时追加 `其它指标`；无 `module_order` 时行为不变。
- 不新增代码注释。
- 无自动化测试；验证 = `npx tsc --noEmit` + 浏览器目检。
- 未经用户明确同意不 `git commit`。

---

### Task 1: `toGroups` 追加"其它指标"组

**Files:** Modify `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx:46-50`

- [ ] **Step 1: 替换 `toGroups` 的返回段**

将：

```tsx
  return {
    groups: moduleOrder.filter((g: string) => groups.has(g))
      .map((name) => ({ name, items: sortByColor(groups.get(name)!) })),
    flat: sortByColor(flat),
  };
```

替换为：

```tsx
  const namedGroups = moduleOrder
    .filter((g: string) => groups.has(g))
    .map((name) => ({ name, items: sortByColor(groups.get(name)!) }));
  if (namedGroups.length > 0 && flat.length > 0) {
    namedGroups.push({ name: '其它指标', items: sortByColor(flat) });
    return { groups: namedGroups, flat: [] };
  }
  return { groups: namedGroups, flat: sortByColor(flat) };
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend/packages/user-portal && npx tsc --noEmit`
Expected: 无输出，exit 0；无 `.js` 残留。

- [ ] **Step 3: 浏览器目检（:3001，user6 报告）**

- 有命名分组且有未归类真指标 → 末尾出现可折叠"其它指标"组，组头 N项 + 红/黄/绿计数正确。
- 展开/收起正常，默认收起。
- 全部归类的报告 → 无"其它指标"组。
- 无 module_order 的旧报告 → 仍整体平铺。

- [ ] **Step 4: 提交（需用户同意后执行）**

```bash
git add frontend/packages/user-portal/src/pages/ReportDetailPage.tsx
git commit -m "feat(user-portal): 未归类指标收进可折叠的其它指标组"
```

## Self-Review

- Spec coverage：两种 module_order 情形 + 边界均有对应实现/验证步骤。
- Placeholder scan：无。
- Type consistency：`namedGroups` 元素 `{name: string; items: any[]}`，与 Collapse `items` 消费一致。
