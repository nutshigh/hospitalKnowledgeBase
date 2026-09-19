# 用户端报告详情：人体健康图（底部弹出面板）

日期：2026-09-18
状态：已实现（见 docs/superpowers/plans/2026-09-18-user-portal-body-health-map.md）
范围：仅 `frontend/packages/user-portal`（患者端 :3001），不改后端/API/doctor-portal/admin-portal。

## 背景与诉求

用户端报告详情页 `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx` 目前只以文字/列表
形式展示红黄绿指标。用户希望增加一个**人体示意图**，把本次报告的红区/黄区异常按器官位置标出来，
更直观。

诉求（用户逐条确认）：

1. 默认**不直接展示**，页面上有一个按钮/链接。
2. 点击后**底部弹出面板**（"tab"），面板里是人体图。
3. **点击其他地方（遮罩）可收起**。
4. 只标**红区 + 黄区异常**（绿区正常项不上图）。
5. 无法归到具体器官的异常（如血脂异常、贫血、血压正常高值）放在**人体图下方的独立列表**，不遗漏。
6. 示例图四角的身高/体重/收缩压/舒张压**不包含**，只显示异常标签。
7. 人体背景图已提取为仓库根目录 `body_background.jpg`（536×825），直接使用。

## 方案（路线 A：前端静态映射表 + 底部面板）

零后端改动、零 DB 迁移。渲染层与映射数据解耦：以后若要把映射挪到后端，只替换数据来源。

### 1. 文件结构

- `src/assets/body_background.jpg` —— 把根目录 `body_background.jpg` 移入，Vite `import` 引入。
  （根目录 `background_example.jpg` 只是参考截图，不动。）
- `src/components/bodyHealthMap/organMap.ts` —— 锚点表 + 关键词规则 + 纯函数
  `resolveAnchor(name)` / `layoutLabels(items)`。
- `src/components/BodyHealthMap.tsx` —— 底部面板组件（antd `Drawer placement="bottom"`）。
- `ReportDetailPage.tsx` —— 摘要条加触发按钮 + 挂载面板。

### 2. 取哪些数据

- 输入 `interpretation.indicators`。
- 过滤 `color_level ∈ {red, yellow}`（`source` 为 `conclusion` 或 `indicator` 都算）。
- 展示名沿用页面现有口径：`explanation || item_name`。
- 无红/黄异常时**不渲染按钮**（面板不可打开）。

### 3. 映射表（核心数据，`organMap.ts`）

锚点 `ANCHORS: Record<string, { x: number; y: number; side: 'left' | 'right' }>`，坐标为图片
百分比（x 左→右，y 上→下，基于 536×825）。首版锚点（实现时按实际图片微调）：

| anchor key | 含义 | x | y | side |
|---|---|---|---|---|
| `head` | 脑/神经 | 50 | 8 | left |
| `eye` | 眼 | 42 | 12 | left |
| `ear` | 耳 | 58 | 12 | right |
| `ent` | 鼻/咽/喉 | 50 | 15 | right |
| `mouth` | 口腔/牙 | 50 | 19 | left |
| `thyroid` | 甲状腺 | 50 | 24 | left |
| `lung` | 肺 | 50 | 36 | left |
| `breast` | 乳腺 | 50 | 38 | right |
| `heart` | 心/循环 | 50 | 42 | left |
| `liver` | 肝 | 40 | 53 | left |
| `gallbladder` | 胆 | 44 | 57 | left |
| `stomach` | 胃 | 58 | 55 | right |
| `spleen` | 脾 | 66 | 55 | right |
| `pancreas` | 胰 | 50 | 58 | right |
| `kidney` | 肾/泌尿指标 | 50 | 61 | right |
| `intestine` | 肠/大便 | 50 | 74 | left |
| `pelvis` | 前列腺/子宫/卵巢/膀胱 | 50 | 80 | right |
| `spine` | 脊柱/骨 | 50 | 50 | left |
| `limbs` | 四肢/关节 | 50 | 65 | right |
| `skin` | 皮肤/全身 | 50 | 45 | left |

规则 `ORGAN_RULES: { re: RegExp; anchor: string }[]`，**有序**，对展示名匹配，命中第一条即停。
首版规则（覆盖器官，顺序关键处已考虑歧义）：

- `甲状腺|甲功|促甲状腺|游离T3|游离T4|TSH|T3|T4|甲状腺素|原氨酸|过氧化物酶抗体` → thyroid
- `脑|神经|头晕|头痛|失眠|记忆|认知|脑血管|经颅` → head
- `眼|视力|眼底|视网膜|角膜|晶状体|晶体|玻璃体|结膜|巩膜|眼压|屈光|白内障|青光眼` → eye
- `耳|听力|鼓膜|耳鸣|耵聍|外耳` → ear
- `鼻|鼻炎|鼻窦|鼻中隔|鼻甲|咽|喉|扁桃体|声带|打鼾|过敏原` → ent
- `口腔|牙|牙龈|龋|舌|腮腺|颞颌|牙周` → mouth
- `肺|呼吸|胸片|胸部CT|肺结节|肺气肿|胸膜|支气管|肺纹理|肺功能` → lung
- `乳腺|乳房` → breast
- `高血压|低血压|血压偏高|血压升高|收缩压|舒张压` → heart
- `心|心律|心率|窦性|心电图|心肌|冠脉|瓣膜|心动|早搏|传导阻滞|ST段|T波` → heart
- `肝|转氨酶|谷丙|谷草|胆红素|脂肪肝|肝囊肿|肝血管瘤|肝内|白蛋白|球蛋白` → liver
- `胆囊|胆石|胆管|胆道|胆总管` → gallbladder
- `胃|幽门|胃炎|胃镜|胃息肉` → stomach
- `胰腺|胰` → pancreas
- `脾` → spleen
- `肾|肾结石|肾囊肿|肌酐|尿素|尿酸|肾小球|肾功|尿蛋白|尿微量|尿潜血|尿隐血` → kidney
- `肠|结肠|直肠|大便|便潜血|隐血|胃肠镜|痔|肛` → intestine
- `前列腺|PSA|膀胱|子宫|卵巢|附件|宫颈|白带|HPV|TCT|液基|盆腔|阴道|外阴|泌尿` → pelvis
- `脊柱|颈椎|腰椎|骨质|骨密度|骨质疏松|椎间盘` → spine
- `关节|四肢|膝|肩|肘|腕|踝|肌力|活动受限` → limbs
- `皮肤|皮疹|湿疹|痣|银屑` → skin

未命中 → `anchor = null` → 进下方“全身性/其他异常”列表。

### 4. 布局算法（纯函数 `layoutLabels`）

- 每个条目经 `resolveAnchor` 得到锚点；按锚点 `side` 分左右两列。
- **选取**：每列红区优先、再按 `anchor.y` 升序取前 **6 个**；未选中的条目回落下方列表（不丢信息）。
- **摆放**：选中的 6 个再按 `anchor.y` 升序自上而下贪心排，行间距最小 6%；整体超出下界 `94%`
  时统一上移（上限 6 个、间距 6% 保证总能放下）。
- 返回每个标签的 `{ x, y, anchorX, anchorY }`（百分比），供渲染。

### 5. 渲染

- 容器 `position: relative; width: 100%; aspect-ratio: 536 / 825`；背景 `img` 铺满。
- SVG 覆盖层 `viewBox="0 0 100 100" preserveAspectRatio="none"` 画引线，坐标直接用百分比；
  `vector-effect="non-scaling-stroke"` 保证线宽不随缩放变形。
- 标签绝对定位：左列贴容器左内缘（右对齐），右列贴容器右内缘（左对齐）；引线从标签内边缘
  连到锚点。红/黄配色（红用 `var(--color-red)`，黄用 `var(--color-yellow)`）。
- 同一器官多个条目：各自独立标签，引线汇聚到同一锚点。

### 6. 面板 UI 与交互

- `Drawer placement="bottom"`，`maskClosable` 点遮罩关闭 + 右上角关闭按钮；内容可滚动。
- 结构：标题「人体健康图」→ 人体图（背景 + 引线 + 标签）→ 下方「全身性/其他异常」列表
  （彩色圆点 + 展示名 + 结果值，风格贴近 `IndicatorRow`）。
- 图例：红区 / 黄区。
- **人体图无标签时**（异常都被清洗或无法定位）：中性提示「人体图暂未显示可定位的异常，详情请
  查看下方「全身性 / 其他异常」」；无列表项时改为「详情请查看报告指标列表」。措辞不做绝对断言
  （避免给基本健康者造成「一定有异常」的暗示）。

### 7. 集成点

摘要条（`红区 x | 黄区 y | 绿区 z`）右侧加按钮「人体健康图」，点击打开面板。

### 8. 展示层清洗（2026-09-18 补，方案 B）

结论提取偶发从「诊断和建议」科普段抠出片段/方法名，且同一发现产生多个名称变体。人体图在
`layoutLabels` 入口做展示层兜底（纯函数，均在 `organMap.ts`，只影响人体图，不改解析链）：

- `isNonFinding(name)`：剔除方向/总结词（升高/降低/偏高…）、碎片泛词（某些药物/炎症感染/曾经…）
  与纯检查/方法名（胸部CT平扫/诊室血压/心电图…）。
- `normalizeFindingName(name)`：去空白/括号内容/前导「曾经」/前导描述词（较大/多发/局部…）/
  尾缀「可能」/尾随尺寸或数值+单位，作为去重键（不含裸数字尾，避免误并）。
- `dedupeFindings(items)`：归一后相等、或**同锚点下互相包含**（≥3 字）判同一实体，保留更完整名称。
- `isExplanatory(text)`：识别「诊断和建议」科普/建议句片段（含 常见于/多见于/表现为/建议/随访/
  治疗/复查/病人/患者/等$/或 等特征）。判定优先用接口返回的 **`origin_line`**（结论条目在
  `conclusion_text` 里的原文句），无则退回名称。
- **口径（用户 2026-09-18）：可以漏、不能多** —— `isNonFinding` 或 `isExplanatory` 命中的条目
  **直接丢弃**（不上人体图、也不进下方列表），宁可漏真发现也不多出噪声。
  已知代价：标题行自带「，建议：」的真发现（如 `肝左叶中等不均回声`）会被误漏。
- 顺序：先按 `isNonFinding(name)` + `isExplanatory(originLine || name)` 过滤，再 `dedupeFindings`，
  之后才映射/分列。避免重复项占满列上限把真发现（如 前列腺钙化灶）挤到下方列表。

实测（H003-29 包雁飞）：21 条红/黄 → 8 个人体标签 + 3 条全身性列表项，真发现无丢失。

## 边界与错误处理

- `interpretation` 为空或无红/黄异常：不渲染触发按钮。
- 所有异常都未命中锚点：人体图只显示背景（无标签），全部进下方列表。
- 某列超过 6 个：超出条目进下方列表，不丢失。
- 展示名可能为结论型（无 `result_value`）：列表行只显示名称 + 色点。

## 测试与验证

前端无测试框架（package.json 无 test 脚本）。纯函数用 Node v24 内置 `node:test`（type stripping，
**零新依赖**）测试，测试文件放 `tests/` 并从 tsconfig `exclude`。

- 纯函数测试：`cd frontend/packages/user-portal && node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/organMap.test.ts`。
- 类型检查：`npx tsc --noEmit` 必须通过。
- 打包：`npx vite build` 必须通过（**不要用 `npm run build`**：其 `tsc` 会把 `.js` 产物写进 `src/`）。
- 手动验收（dev :3001，任一带红/黄异常的报告）：
  - 默认页面上无人体图，只有「人体健康图」按钮。
  - 点击按钮 → 底部面板弹出，人体图上出现红/黄标签与引线，器官位置合理。
  - 点遮罩/关闭按钮 → 面板收起。
  - 无器官归属的异常出现在下方“全身性/其他异常”列表。
  - 全绿报告：无按钮。
- `organMap.ts` 的 `resolveAnchor` / `layoutLabels` 保持纯函数，便于日后引入 vitest 补测
  （本次不引入测试框架）。

## 非目标（YAGNI）

- 不改后端 / API / DB；不改 doctor-portal、admin-portal。
- 不显示身高/体重/血压等基础体征四角卡片。
- 不做标签点击跳转/高亮联动。
- 不做绿区正常项展示。
- 不引入前端测试框架。
