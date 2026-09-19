# 用户端报告详情：人体健康图 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在用户端报告详情页加一个默认收起、点击后底部弹出的「人体健康图」，把本次报告的红区/黄区异常按器官位置标在人体背景图上，无法定位的异常列在下方。

**Architecture:** 纯前端、零后端改动。`organMap.ts` 提供器官锚点表、关键词映射与纯函数布局算法；`BodyHealthMap.tsx` 用 antd `Drawer placement="bottom"` 渲染背景图 + SVG 引线 + 绝对定位标签 + 下方列表；`ReportDetailPage.tsx` 加触发按钮与状态。

**Tech Stack:** React 18 + TypeScript + antd 5 + Vite 6；纯函数测试用 Node v24 内置 `node:test`（type stripping，零新依赖）。

## Global Constraints

- 仅修改 `frontend/packages/user-portal`，不改后端 / API / DB / doctor-portal / admin-portal。
- **不新增任何 npm 依赖**。
- **不要执行 `git commit`**，除非用户明确要求；计划中的 Commit 步骤视为检查点，暂停等用户确认。
- **不要用 `npm run build` 做验证**：其 `tsc` 会把 `.js` 产物写进 `src/`（已确认的仓库坏味道）。验证用 `npx tsc --noEmit`（类型检查）与 `npx vite build`（打包）。
- 所有命令在 `frontend/packages/user-portal` 目录下执行。
- `src/components/bodyHealthMap/organMap.ts` 必须是纯 TS：不得 import React / antd / 图片 / 浏览器 API。
- 测试文件放 `tests/`，并从 tsconfig 中 `exclude`，避免 `tsc` 类型检查测试文件（测试 import 用 `.ts` 扩展名）。
- 测试命令：`node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/organMap.test.ts`。

> **实现后修正（2026-09-18，final review 后）**：Task 1 Step 4 代码块里的 `ORGAN_RULES` 为初版，已按最终评审修正，以 `docs/superpowers/specs/2026-09-18-user-portal-body-health-map-design.md` §3 与代码为准：
> - 胆囊规则 `/胆囊|胆石|胆管|胆道|胆总管/`（去掉裸 `胆`，否则 `总胆固醇`/`高密度脂蛋白胆固醇`/`低密度脂蛋白胆固醇` 被误配到胆囊）。
> - 肾/泌尿规则追加 `尿潜血|尿隐血`（`尿潜血(BLD)` 规范名此前未映射；`便潜血` 仍归肠道）。
> - 血压规则改为 `/高血压|低血压|血压偏高|血压升高|收缩压|舒张压/`（`血压正常高值` 落下方列表，符合需求 5）。
> 下方代码块未回改，勿直接照抄。

---

### Task 1: 器官映射模块 `resolveAnchor`

**Files:**
- Create: `frontend/packages/user-portal/src/components/bodyHealthMap/organMap.ts`
- Create: `frontend/packages/user-portal/tests/organMap.test.ts`
- Modify: `frontend/packages/user-portal/tsconfig.json`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `type ColorLevel = 'red' | 'yellow'`
  - `interface Anchor { x: number; y: number; side: 'left' | 'right' }`
  - `const ANCHORS: Record<string, Anchor>`
  - `function resolveAnchor(name: string): string | null`

- [ ] **Step 1: 让 `tsc` 忽略 `tests/`**

把 `frontend/packages/user-portal/tsconfig.json` 改为：

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "esModuleInterop": true,
    "jsx": "react-jsx",
    "skipLibCheck": true
  },
  "exclude": ["tests"]
}
```

- [ ] **Step 2: 写失败的测试**

创建 `frontend/packages/user-portal/tests/organMap.test.ts`：

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveAnchor } from '../src/components/bodyHealthMap/organMap.ts';

test('resolveAnchor maps common findings to organs', () => {
  assert.equal(resolveAnchor('甲状腺囊肿'), 'thyroid');
  assert.equal(resolveAnchor('窦性心动过缓'), 'heart');
  assert.equal(resolveAnchor('谷丙转氨酶偏高'), 'liver');
  assert.equal(resolveAnchor('胆囊息肉样病变'), 'gallbladder');
  assert.equal(resolveAnchor('双肺散在小结节'), 'lung');
  assert.equal(resolveAnchor('龋齿'), 'mouth');
  assert.equal(resolveAnchor('变应性鼻炎'), 'ent');
  assert.equal(resolveAnchor('尿酸'), 'kidney');
});

test('resolveAnchor returns null for systemic findings', () => {
  assert.equal(resolveAnchor('血脂异常'), null);
  assert.equal(resolveAnchor('贫血'), null);
  assert.equal(resolveAnchor(''), null);
});
```

- [ ] **Step 3: 运行测试确认失败**

Run: `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/organMap.test.ts`
Expected: FAIL，报找不到模块 `organMap.ts` / `resolveAnchor` 未定义。

- [ ] **Step 4: 实现锚点表与 `resolveAnchor`**

创建 `frontend/packages/user-portal/src/components/bodyHealthMap/organMap.ts`：

```ts
export type ColorLevel = 'red' | 'yellow';

export interface Anchor {
  x: number;
  y: number;
  side: 'left' | 'right';
}

// 坐标 = 人体背景图(body_background.jpg, 536x825)的百分比。
export const ANCHORS: Record<string, Anchor> = {
  head:        { x: 50, y: 8,  side: 'left' },
  eye:         { x: 42, y: 12, side: 'left' },
  ear:         { x: 58, y: 12, side: 'right' },
  ent:         { x: 50, y: 15, side: 'right' },
  mouth:       { x: 50, y: 19, side: 'left' },
  thyroid:     { x: 50, y: 24, side: 'left' },
  lung:        { x: 50, y: 36, side: 'left' },
  breast:      { x: 50, y: 38, side: 'right' },
  heart:       { x: 50, y: 42, side: 'left' },
  liver:       { x: 40, y: 53, side: 'left' },
  gallbladder: { x: 44, y: 57, side: 'left' },
  stomach:     { x: 58, y: 55, side: 'right' },
  spleen:      { x: 66, y: 55, side: 'right' },
  pancreas:    { x: 50, y: 58, side: 'right' },
  kidney:      { x: 50, y: 61, side: 'right' },
  intestine:   { x: 50, y: 74, side: 'left' },
  pelvis:      { x: 50, y: 80, side: 'right' },
  spine:       { x: 50, y: 50, side: 'left' },
  limbs:       { x: 50, y: 65, side: 'right' },
  skin:        { x: 50, y: 45, side: 'left' },
};

// 有序规则，命中第一条即停。顺序对歧义项很重要(如 尿酸 必须先于 泌尿)。
export const ORGAN_RULES: { re: RegExp; anchor: string }[] = [
  { re: /甲状腺|甲功|促甲状腺|游离T3|游离T4|TSH|T3|T4|甲状腺素|原氨酸|过氧化物酶抗体/, anchor: 'thyroid' },
  { re: /脑|神经|头晕|头痛|失眠|记忆|认知|脑血管|经颅/, anchor: 'head' },
  { re: /眼|视力|眼底|视网膜|角膜|晶状体|晶体|玻璃体|结膜|巩膜|眼压|屈光|白内障|青光眼/, anchor: 'eye' },
  { re: /耳|听力|鼓膜|耳鸣|耵聍|外耳/, anchor: 'ear' },
  { re: /鼻|鼻炎|鼻窦|鼻中隔|鼻甲|咽|喉|扁桃体|声带|打鼾|过敏原/, anchor: 'ent' },
  { re: /口腔|牙|牙龈|龋|舌|腮腺|颞颌|牙周/, anchor: 'mouth' },
  { re: /肺|呼吸|胸片|胸部CT|肺结节|肺气肿|胸膜|支气管|肺纹理|肺功能/, anchor: 'lung' },
  { re: /乳腺|乳房/, anchor: 'breast' },
  { re: /血压/, anchor: 'heart' },
  { re: /心|心律|心率|窦性|心电图|心肌|冠脉|瓣膜|心动|早搏|传导阻滞|ST段|T波/, anchor: 'heart' },
  { re: /肝|转氨酶|谷丙|谷草|胆红素|脂肪肝|肝囊肿|肝血管瘤|肝内|白蛋白|球蛋白/, anchor: 'liver' },
  { re: /胆囊|胆石|胆/, anchor: 'gallbladder' },
  { re: /胃|幽门|胃炎|胃镜|胃息肉/, anchor: 'stomach' },
  { re: /胰腺|胰/, anchor: 'pancreas' },
  { re: /脾/, anchor: 'spleen' },
  { re: /肾|肾结石|肾囊肿|肌酐|尿素|尿酸|肾小球|肾功|尿蛋白|尿微量/, anchor: 'kidney' },
  { re: /肠|结肠|直肠|大便|便潜血|隐血|胃肠镜|痔|肛/, anchor: 'intestine' },
  { re: /前列腺|PSA|膀胱|子宫|卵巢|附件|宫颈|白带|HPV|TCT|液基|盆腔|阴道|外阴|泌尿/, anchor: 'pelvis' },
  { re: /脊柱|颈椎|腰椎|骨质|骨密度|骨质疏松|椎间盘/, anchor: 'spine' },
  { re: /关节|四肢|膝|肩|肘|腕|踝|肌力|活动受限/, anchor: 'limbs' },
  { re: /皮肤|皮疹|湿疹|痣|银屑/, anchor: 'skin' },
];

export function resolveAnchor(name: string): string | null {
  if (!name) return null;
  const s = name.replace(/\s+/g, '');
  for (const rule of ORGAN_RULES) {
    if (rule.re.test(s)) return rule.anchor;
  }
  return null;
}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/organMap.test.ts`
Expected: PASS，2 个测试全过。

- [ ] **Step 6: 类型检查**

Run: `npx tsc --noEmit`
Expected: 退出码 0，无输出。

- [ ] **Step 7: 检查点（用户确认后再提交）**

```bash
git add frontend/packages/user-portal/tsconfig.json \
        frontend/packages/user-portal/src/components/bodyHealthMap/organMap.ts \
        frontend/packages/user-portal/tests/organMap.test.ts
git commit -m "feat(user-portal): add organ mapping for body health map"
```

---

### Task 2: 布局算法 `layoutLabels`

**Files:**
- Modify: `frontend/packages/user-portal/src/components/bodyHealthMap/organMap.ts`
- Modify: `frontend/packages/user-portal/tests/organMap.test.ts`

**Interfaces:**
- Consumes: `ANCHORS`, `resolveAnchor`, `ColorLevel`（Task 1）。
- Produces:
  - `interface AbnItem { name: string; level: ColorLevel; value?: string | null }`
  - `interface PlacedLabel extends AbnItem { anchorKey: string; x: number; y: number; anchorX: number; anchorY: number }`
  - `interface LayoutResult { labels: PlacedLabel[]; others: AbnItem[] }`
  - `function layoutLabels(items: AbnItem[]): LayoutResult`

- [ ] **Step 1: 写失败的测试**

在 `tests/organMap.test.ts` 顶部 import 里补 `layoutLabels`，并追加以下测试：

```ts
import { resolveAnchor, layoutLabels } from '../src/components/bodyHealthMap/organMap.ts';
```

```ts
test('layoutLabels splits mapped and unmapped', () => {
  const { labels, others } = layoutLabels([
    { name: '甲状腺囊肿', level: 'red' },
    { name: '血脂异常', level: 'yellow' },
  ]);
  assert.equal(labels.length, 1);
  assert.equal(labels[0].anchorKey, 'thyroid');
  assert.equal(others.length, 1);
  assert.equal(others[0].name, '血脂异常');
});

test('layoutLabels caps each side and pushes overflow to others', () => {
  const items = Array.from({ length: 8 }, (_, i) => ({ name: `心律不齐${i}`, level: 'yellow' as const }));
  const { labels, others } = layoutLabels(items);
  assert.equal(labels.length, 6);
  assert.equal(others.length, 2);
});

test('layoutLabels keeps red items when capping', () => {
  const items = [
    ...Array.from({ length: 6 }, (_, i) => ({ name: `心律不齐${i}`, level: 'yellow' as const })),
    { name: '窦性心动过缓', level: 'red' as const },
  ];
  const { labels } = layoutLabels(items);
  assert.ok(labels.some((l) => l.level === 'red'));
});

test('layoutLabels assigns side x and keeps y within bounds', () => {
  const { labels } = layoutLabels([
    { name: '甲状腺囊肿', level: 'red' },
    { name: '胃息肉', level: 'yellow' },
  ]);
  const thyroid = labels.find((l) => l.anchorKey === 'thyroid')!;
  const stomach = labels.find((l) => l.anchorKey === 'stomach')!;
  assert.equal(thyroid.x, 34);
  assert.equal(stomach.x, 66);
  for (const l of labels) {
    assert.ok(l.y >= 6 && l.y <= 94, `y ${l.y} out of range`);
    assert.ok(l.anchorX >= 0 && l.anchorX <= 100);
    assert.ok(l.anchorY >= 0 && l.anchorY <= 100);
  }
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/organMap.test.ts`
Expected: FAIL，报 `layoutLabels` 未定义/不是函数。

- [ ] **Step 3: 实现 `layoutLabels`**

在 `organMap.ts` 末尾追加：

```ts
export interface AbnItem {
  name: string;
  level: ColorLevel;
  value?: string | null;
}

export interface PlacedLabel extends AbnItem {
  anchorKey: string;
  x: number;
  y: number;
  anchorX: number;
  anchorY: number;
}

export interface LayoutResult {
  labels: PlacedLabel[];
  others: AbnItem[];
}

const LEFT_X = 34;
const RIGHT_X = 66;
const TOP = 6;
const BOTTOM = 94;
const MIN_GAP = 6;
const MAX_PER_SIDE = 6;

const LEVEL_RANK: Record<ColorLevel, number> = { red: 0, yellow: 1 };

export function layoutLabels(items: AbnItem[]): LayoutResult {
  const mapped: { item: AbnItem; anchorKey: string; anchor: Anchor }[] = [];
  const others: AbnItem[] = [];
  for (const item of items) {
    const anchorKey = resolveAnchor(item.name);
    if (anchorKey && ANCHORS[anchorKey]) {
      mapped.push({ item, anchorKey, anchor: ANCHORS[anchorKey] });
    } else {
      others.push(item);
    }
  }

  const sides: Record<'left' | 'right', typeof mapped> = { left: [], right: [] };
  for (const m of mapped) sides[m.anchor.side].push(m);

  const labels: PlacedLabel[] = [];
  for (const side of ['left', 'right'] as const) {
    const list = sides[side];
    // 选取：红区优先，再按锚点 y；取前 MAX_PER_SIDE，其余回落下方列表。
    const picked = [...list]
      .sort((a, b) =>
        LEVEL_RANK[a.item.level] - LEVEL_RANK[b.item.level] ||
        a.anchor.y - b.anchor.y ||
        a.item.name.localeCompare(b.item.name))
      .slice(0, MAX_PER_SIDE);
    for (const m of list) {
      if (!picked.includes(m)) others.push(m.item);
    }
    // 摆放：按锚点 y 自上而下，最小行距 MIN_GAP；整体超出下界则上移。
    picked.sort((a, b) => a.anchor.y - b.anchor.y || a.item.name.localeCompare(b.item.name));
    let prev = TOP - MIN_GAP;
    const placed = picked.map((m) => {
      const y = Math.max(m.anchor.y, prev + MIN_GAP);
      prev = y;
      return { m, y };
    });
    if (placed.length) {
      const last = placed[placed.length - 1].y;
      if (last > BOTTOM) {
        const shift = last - BOTTOM;
        for (const p of placed) p.y = Math.max(TOP, p.y - shift);
      }
    }
    for (const { m, y } of placed) {
      labels.push({
        ...m.item,
        anchorKey: m.anchorKey,
        x: side === 'left' ? LEFT_X : RIGHT_X,
        y,
        anchorX: m.anchor.x,
        anchorY: m.anchor.y,
      });
    }
  }

  others.sort((a, b) => LEVEL_RANK[a.level] - LEVEL_RANK[b.level] || a.name.localeCompare(b.name));
  return { labels, others };
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/organMap.test.ts`
Expected: PASS，6 个测试全过。

- [ ] **Step 5: 类型检查**

Run: `npx tsc --noEmit`
Expected: 退出码 0。

- [ ] **Step 6: 检查点（用户确认后再提交）**

```bash
git add frontend/packages/user-portal/src/components/bodyHealthMap/organMap.ts \
        frontend/packages/user-portal/tests/organMap.test.ts
git commit -m "feat(user-portal): add body health map label layout algorithm"
```

---

### Task 3: 面板组件 `BodyHealthMap` 与背景图资源

**Files:**
- Create: `frontend/packages/user-portal/src/assets/body_background.jpg`
- Create: `frontend/packages/user-portal/src/images.d.ts`
- Create: `frontend/packages/user-portal/src/components/BodyHealthMap.tsx`

**Interfaces:**
- Consumes: `layoutLabels`, `PlacedLabel`, `AbnItem`（Task 2）。
- Produces: `default export function BodyHealthMap(props: { open: boolean; onClose: () => void; indicators: any[] })`

- [ ] **Step 1: 放入背景图**

Run:
```bash
mkdir -p src/assets
cp ../../../body_background.jpg src/assets/body_background.jpg
ls -l src/assets/body_background.jpg
```
Expected: 文件存在（约 29KB）。

- [ ] **Step 2: 声明图片模块类型**

创建 `frontend/packages/user-portal/src/images.d.ts`：

```ts
declare module '*.jpg' {
  const src: string;
  export default src;
}
```

- [ ] **Step 3: 实现组件**

创建 `frontend/packages/user-portal/src/components/BodyHealthMap.tsx`：

```tsx
import { useMemo } from 'react';
import { Drawer } from 'antd';
import bodyBg from '../assets/body_background.jpg';
import { layoutLabels, type ColorLevel } from './bodyHealthMap/organMap';

interface BodyHealthMapProps {
  open: boolean;
  onClose: () => void;
  indicators: any[];
}

const LEVEL_COLOR: Record<ColorLevel, string> = {
  red: 'var(--color-red)',
  yellow: 'var(--color-yellow)',
};
const LEVEL_BG: Record<ColorLevel, string> = {
  red: 'var(--color-red-light)',
  yellow: 'var(--color-yellow-light)',
};
const DOT = { display: 'inline-block', width: 8, height: 8, borderRadius: '50%', marginRight: 4 } as const;

export default function BodyHealthMap({ open, onClose, indicators }: BodyHealthMapProps) {
  const abnormal = useMemo(
    () => (indicators || [])
      .filter((i: any) => i.color_level === 'red' || i.color_level === 'yellow')
      .map((i: any) => ({
        name: i.explanation || i.item_name,
        level: i.color_level as ColorLevel,
        value: i.result_value,
      })),
    [indicators],
  );
  const { labels, others } = useMemo(() => layoutLabels(abnormal), [abnormal]);

  return (
    <Drawer
      placement="bottom"
      open={open}
      onClose={onClose}
      height="88vh"
      title="人体健康图"
      styles={{ body: { padding: '12px 16px 24px' } }}
    >
      <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 8 }}>
        <span><span style={{ ...DOT, background: LEVEL_COLOR.red }} />红区</span>
        <span><span style={{ ...DOT, background: LEVEL_COLOR.yellow }} />黄区</span>
      </div>

      <div style={{ position: 'relative', width: '100%', maxWidth: 340, margin: '0 auto', aspectRatio: '536 / 825' }}>
        <img src={bodyBg} alt="人体示意图" style={{ width: '100%', display: 'block' }} />
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
        >
          {labels.map((l, i) => (
            <line
              key={i}
              x1={l.x} y1={l.y} x2={l.anchorX} y2={l.anchorY}
              stroke={LEVEL_COLOR[l.level]}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {labels.map((l, i) => (
          <div
            key={i}
            style={{
              position: 'absolute',
              top: `${l.y}%`,
              left: `${l.x}%`,
              transform: l.x < 50 ? 'translate(-100%, -50%)' : 'translate(0, -50%)',
              maxWidth: 120,
            }}
          >
            <span style={{
              display: 'inline-block',
              padding: '2px 8px',
              borderRadius: 12,
              fontSize: 12,
              fontWeight: 600,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              maxWidth: 120,
              color: LEVEL_COLOR[l.level],
              background: LEVEL_BG[l.level],
              border: `1px solid ${LEVEL_COLOR[l.level]}`,
            }}>
              {l.name}
            </span>
          </div>
        ))}
      </div>

      {others.length > 0 && (
        <div style={{ marginTop: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>全身性 / 其他异常</div>
          {others.map((o, i) => (
            <div
              key={i}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '10px 0', borderBottom: '1px solid var(--color-border-light)',
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: LEVEL_COLOR[o.level], flexShrink: 0 }} />
              <span style={{ flex: 1, fontSize: 14 }}>{o.name}</span>
              {o.value ? <span style={{ fontSize: 14, fontWeight: 600 }}>{o.value}</span> : null}
            </div>
          ))}
        </div>
      )}
    </Drawer>
  );
}
```

- [ ] **Step 4: 类型检查**

Run: `npx tsc --noEmit`
Expected: 退出码 0。

- [ ] **Step 5: 打包验证**

Run: `npx vite build`
Expected: 构建成功，`dist/assets/` 下出现 body_background 的图片产物（哈希文件名）。

- [ ] **Step 6: 检查点（用户确认后再提交）**

```bash
git add frontend/packages/user-portal/src/assets/body_background.jpg \
        frontend/packages/user-portal/src/images.d.ts \
        frontend/packages/user-portal/src/components/BodyHealthMap.tsx
git commit -m "feat(user-portal): add body health map drawer component"
```

---

### Task 4: 集成进报告详情页

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`

**Interfaces:**
- Consumes: `BodyHealthMap`（Task 3）。
- Produces: 页面摘要条上的「人体健康图」按钮与受控 `Drawer`。

- [ ] **Step 1: 加 import**

在 `ReportDetailPage.tsx` 顶部 import 区（`import ChatPanel from '../components/ChatPanel';` 之后）加：

```tsx
import BodyHealthMap from '../components/BodyHealthMap';
```

- [ ] **Step 2: 加状态**

在 `const [openModules, setOpenModules] = useState<string[]>([]);` 之后加：

```tsx
const [bodyMapOpen, setBodyMapOpen] = useState(false);
```

- [ ] **Step 3: 派生 `hasAbnormal`**

在 `const moduleOrder = interpretation?.module_order ?? report?.module_order;` 之后加：

```tsx
const hasAbnormal = rawIndicators.some(
  (ind: any) => ind.color_level === 'red' || ind.color_level === 'yellow',
);
```

- [ ] **Step 4: 摘要条加按钮**

把现有的摘要条整块（`{interpretation && ( ... )}`，约 289-299 行）替换为：

```tsx
{interpretation && (
  <div style={{
    display: 'flex', gap: 8, marginBottom: 16, padding: '12px 16px', background: 'var(--color-bg)',
    borderRadius: 'var(--radius-sm)', alignItems: 'center',
  }}>
    <span style={{ color: 'var(--color-red)', fontWeight: 600, fontSize: 13 }}>红区 {levelCounts.red}</span>
    <span style={{ color: 'var(--color-border)' }}>|</span>
    <span style={{ color: 'var(--color-yellow)', fontWeight: 600, fontSize: 13 }}>黄区 {levelCounts.yellow}</span>
    <span style={{ color: 'var(--color-border)' }}>|</span>
    <span style={{ color: 'var(--color-green)', fontWeight: 600, fontSize: 13 }}>绿区 {levelCounts.green}</span>
    {hasAbnormal && (
      <button
        onClick={() => setBodyMapOpen(true)}
        style={{
          marginLeft: 'auto', border: '1px solid var(--color-primary)', background: 'var(--color-surface)',
          color: 'var(--color-primary)', borderRadius: 16, padding: '4px 12px',
          fontSize: 13, fontWeight: 600, cursor: 'pointer',
        }}
      >
        人体健康图
      </button>
    )}
  </div>
)}
```

- [ ] **Step 5: 挂载面板**

在 `return` 内 `</Layout>` 之前（`{chatSessionId && ( ... )}` 块之后）加：

```tsx
<BodyHealthMap
  open={bodyMapOpen}
  onClose={() => setBodyMapOpen(false)}
  indicators={rawIndicators}
/>
```

- [ ] **Step 6: 类型检查**

Run: `npx tsc --noEmit`
Expected: 退出码 0。

- [ ] **Step 7: 打包验证**

Run: `npx vite build`
Expected: 构建成功。

- [ ] **Step 8: 检查点（用户确认后再提交）**

```bash
git add frontend/packages/user-portal/src/pages/ReportDetailPage.tsx
git commit -m "feat(user-portal): wire body health map into report detail page"
```

---

### Task 5: 最终验证与清理

**Files:** 无新增；确认工作区干净。

- [ ] **Step 1: 全量纯函数测试**

Run: `node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/organMap.test.ts`
Expected: PASS，6 个测试全过。

- [ ] **Step 2: 类型检查**

Run: `npx tsc --noEmit`
Expected: 退出码 0。

- [ ] **Step 3: 打包**

Run: `npx vite build`
Expected: 成功。

- [ ] **Step 4: 确认没有 tsc 生成的 `.js` 污染**

Run: `find src -name '*.js'`
Expected: 无输出（若用 `npm run build` 会产生，需 `find src -name '*.js' -delete` 清理）。

- [ ] **Step 5: 手动浏览器验收**

Run: `npx vite --port 3001`（或 `bash ../../start_front_user.sh`），用带红/黄异常的报告打开报告详情：
- 默认页面上没有人体图，只有摘要条右侧的「人体健康图」按钮。
- 点击按钮 → 底部面板滑出，人体图上出现红/黄标签与引线，器官位置合理。
- 点遮罩 / 关闭按钮 → 面板收起。
- 无器官归属的异常出现在面板下方「全身性 / 其他异常」列表。
- 全绿报告（`hasAbnormal=false`）：摘要条无按钮。

- [ ] **Step 6: 更新 spec 状态**

把 `docs/superpowers/specs/2026-09-18-user-portal-body-health-map-design.md` 的「状态」行改为：

```
状态：已实现（见 docs/superpowers/plans/2026-09-18-user-portal-body-health-map.md）
```

- [ ] **Step 7: 检查点（用户确认后再提交）**

```bash
git add docs/superpowers/specs/2026-09-18-user-portal-body-health-map-design.md
git commit -m "docs: mark body health map spec implemented"
```
