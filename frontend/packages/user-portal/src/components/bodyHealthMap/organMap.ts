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
  { re: /高血压|低血压|血压偏高|血压升高|收缩压|舒张压/, anchor: 'heart' },
  { re: /心|心律|心率|窦性|心电图|心肌|冠脉|瓣膜|心动|早搏|传导阻滞|ST段|T波/, anchor: 'heart' },
  { re: /肝|转氨酶|谷丙|谷草|胆红素|脂肪肝|肝囊肿|肝血管瘤|肝内|白蛋白|球蛋白/, anchor: 'liver' },
  { re: /胆囊|胆石|胆管|胆道|胆总管/, anchor: 'gallbladder' },
  { re: /胃|幽门|胃炎|胃镜|胃息肉/, anchor: 'stomach' },
  { re: /胰腺|胰/, anchor: 'pancreas' },
  { re: /脾/, anchor: 'spleen' },
  { re: /肾|肾结石|肾囊肿|肌酐|尿素|尿酸|肾小球|肾功|尿蛋白|尿微量|尿潜血|尿隐血/, anchor: 'kidney' },
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

// 非"发现"条目: 方向/总结词、碎片泛词、纯检查/方法名 —— 人体图不展示。
const DIRECTION_RE = /^(?:升高|降低|偏高|偏低|增高|减少|下降|正常|异常|阴性|阳性|无|未见|阳性结果|阴性结果)$/;
const FRAGMENT_RE = /^(?:某些药物|药物|炎症感染|曾经炎症感染|感染|诊室血压|胸部CT平扫|胸部X片|胸片|彩超|B超|超声|心电图|胃镜|肠镜|CT|MRI|核磁|X线|X光|血常规|尿常规|肝功能|肾功能|血脂|血糖|身高|体重|心率|脉搏)$/;
const METHOD_RE = /^(?:胸部|头颅|腹部|盆腔|颈椎|腰椎|双侧)?(?:CT|MRI|核磁|X线|X光|彩超|B超|超声|心电图|胃镜|肠镜|肺功能|骨密度)(?:平扫|增强|检查)?$/;

export function isNonFinding(name: string): boolean {
  const s = (name || '').replace(/\s+/g, '').replace(/^曾经/, '');
  return DIRECTION_RE.test(s) || FRAGMENT_RE.test(s) || METHOD_RE.test(s);
}

// 解释/建议句片段(从「诊断和建议」科普段误抽): 不参与器官映射, 直接进下方列表。
const EXPLANATORY_RE = /常见于|多见于|可分为|表现为|尚未|取决于|有关|因素|建议|随访|就诊|诊治|治疗|复查|病人|患者|就医|等$|或/;

export function isExplanatory(name: string): boolean {
  const s = (name || '').replace(/\s+/g, '');
  return EXPLANATORY_RE.test(s);
}

// 同名变体归一(仅用于去重键): 去空白/括号内容/前导"曾经"/前导描述词/尾缀"可能"/尾随尺寸或数值+单位。
export function normalizeFindingName(name: string): string {
  let s = (name || '').replace(/\s+/g, '');
  s = s.replace(/[（(][^）)]*[）)]/g, '');
  s = s.replace(/^曾经/, '');
  s = s.replace(/^(?:较大|较小|大量|少量|多发|单发|局部|散在|弥漫|轻度|中度|重度|块|明显)+/, '');
  s = s.replace(/可能$/, '');
  s = s.replace(/[\d.]+\s*[*×xX]\s*[\d.]+\s*(?:mm|cm)?$/i, '');
  s = s.replace(/[\d.]+\s*(?:mm|cm|ml|mmHg|umol\/l|umol|%|个|项)$/i, '');
  return s;
}

// 合并同一发现的名称变体(归一后相等, 或同锚点下互相包含); 保留更完整的名称。
export function dedupeFindings(items: AbnItem[]): AbnItem[] {
  const out: AbnItem[] = [];
  for (const it of items) {
    const n = normalizeFindingName(it.name);
    const anchor = resolveAnchor(it.name);
    const dup = out.find((o) => {
      if (n === normalizeFindingName(o.name)) return true;
      if (!anchor || anchor !== resolveAnchor(o.name)) return false;
      const on = normalizeFindingName(o.name);
      return n.length >= 3 && on.length >= 3 && (n.includes(on) || on.includes(n));
    });
    if (!dup) {
      out.push({ ...it });
    } else if (it.name.length > dup.name.length) {
      dup.name = it.name;
    }
  }
  return out;
}

export interface AbnItem {
  name: string;
  level: ColorLevel;
  value?: string | null;
  // 结论条目在 conclusion_text 里的原文句(接口 origin_line); 用于更准地识别解释/建议句。
  originLine?: string | null;
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
  // 先剔除方法/碎片等非发现条目与解释/建议句(用 origin_line 更准), 再合并同名变体。
  // 口径(用户 2026-09-18): 宁可漏真发现, 不可多出噪声 —— 解释/建议句直接丢弃。
  const findings = dedupeFindings(
    items.filter((it) => !isNonFinding(it.name) && !isExplanatory(it.originLine || it.name)),
  );
  const mapped: { item: AbnItem; anchorKey: string; anchor: Anchor }[] = [];
  const others: AbnItem[] = [];
  for (const item of findings) {
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
