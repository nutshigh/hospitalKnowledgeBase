import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveAnchor, layoutLabels, isNonFinding, isExplanatory, normalizeFindingName } from '../src/components/bodyHealthMap/organMap.ts';

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

test('resolveAnchor maps cholesterol and biliary findings correctly', () => {
  assert.equal(resolveAnchor('总胆固醇'), null);
  assert.equal(resolveAnchor('高密度脂蛋白胆固醇'), null);
  assert.equal(resolveAnchor('低密度脂蛋白胆固醇'), null);
  assert.equal(resolveAnchor('胆囊息肉样病变'), 'gallbladder');
  assert.equal(resolveAnchor('胆红素'), 'liver');
});

test('resolveAnchor maps urinary occult blood to kidney and stool occult blood to intestine', () => {
  assert.equal(resolveAnchor('尿潜血(BLD)'), 'kidney');
  assert.equal(resolveAnchor('尿隐血'), 'kidney');
  assert.equal(resolveAnchor('便潜血'), 'intestine');
});

test('resolveAnchor maps blood-pressure findings per user decision', () => {
  assert.equal(resolveAnchor('高血压'), 'heart');
  assert.equal(resolveAnchor('低血压'), 'heart');
  assert.equal(resolveAnchor('血压正常高值'), null);
});

test('layoutLabels spaces same-side items and shifts them above the bottom bound', () => {
  const items = Array.from({ length: 6 }, (_, i) => ({ name: `肠息肉${i}`, level: 'red' as const }));
  const { labels, others } = layoutLabels(items);
  assert.equal(labels.length, 6);
  assert.equal(others.length, 0);
  const ys = labels.map((l) => l.y);
  for (let i = 1; i < ys.length; i++) {
    assert.ok(ys[i] - ys[i - 1] >= 6 - 1e-9, `gap ${ys[i] - ys[i - 1]} below 6`);
  }
  assert.ok(Math.max(...ys) <= 94, `max y ${Math.max(...ys)} above 94`);
});

test('layoutLabels passes value through to PlacedLabel', () => {
  const { labels } = layoutLabels([
    { name: '甲状腺囊肿', level: 'red', value: '3.2' },
  ]);
  assert.equal(labels[0].value, '3.2');
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

test('isNonFinding drops method names, fragments and direction words', () => {
  for (const n of ['升高', '降低', '某些药物', '炎症感染', '曾经炎症感染', '胸部CT平扫', '诊室血压']) {
    assert.equal(isNonFinding(n), true, `should be non-finding: ${n}`);
  }
  for (const n of ['前列腺钙化灶', '左肾多发囊肿', '右耳耵聍栓塞', '甲状腺结节']) {
    assert.equal(isNonFinding(n), false, `should be a finding: ${n}`);
  }
});

test('normalizeFindingName strips variants but keeps real digits without units', () => {
  assert.equal(normalizeFindingName('肝左叶中等不均回声29*28mm'), '肝左叶中等不均回声');
  assert.equal(normalizeFindingName('收缩压高140mmHg'), '收缩压高');
  assert.equal(normalizeFindingName('较大块耵聍栓'), '耵聍栓');
  assert.equal(normalizeFindingName('心律不齐0'), '心律不齐0');
});

test('isExplanatory detects explanation/suggestion fragments but not finding titles', () => {
  for (const n of ['生理性改变常见于精神紧张', '病理性改变常见于高血压', '过度吸烟等', '贫血或药物影响等']) {
    assert.equal(isExplanatory(n), true, `should be explanatory: ${n}`);
  }
  for (const n of ['左侧颈动脉局部见斑块形成', '胆囊泥沙样结石', '可疑Q波', 'T波改变', '右侧甲状腺小结节可能']) {
    assert.equal(isExplanatory(n), false, `should be a finding title: ${n}`);
  }
});

test('layoutLabels drops name-level explanation fragments entirely (曹嘉冰 H003-31 names)', () => {
  const names = [
    '黄疸', '高密度脂蛋白胆固醇', '甘油三脂', '低密度脂蛋白胆固醇', '胆囊结石',
    '生理性改变常见于精神紧张', '劳累', '熬夜', '过度吸烟等',
    '病理性改变常见于高血压', '冠心病', '贫血或药物影响等',
  ];
  const { labels, others } = layoutLabels(
    names.map((name) => ({ name, level: (name === '黄疸' ? 'red' : 'yellow') as 'red' | 'yellow' })),
  );
  const all = [...labels, ...others].map((x) => x.name);
  for (const frag of ['生理性改变常见于精神紧张', '病理性改变常见于高血压', '过度吸烟等', '贫血或药物影响等']) {
    assert.ok(!all.includes(frag), `name-level explanation fragment must be dropped: ${frag}`);
  }
});

test('layoutLabels drops conclusion entries whose origin_line is a suggestion (曹嘉冰 H003-31, 可以漏不能多)', () => {
  const withLine = (name: string, originLine: string) => ({ name, level: 'yellow' as const, originLine });
  const items = [
    withLine('黄疸', '出现黄疸时，应立即就医检查，查明黄疸原因，已作相应处理，切勿拖延。'),
    withLine('胆囊结石', '1、胆囊结石病人应少吃肥肉和胆固醇含量高的食物，如蛋黄、鱼卵、蟹黄、动物内脏等食品，不吃油炸。'),
    withLine('生理性改变常见于精神紧张', '1、生理性改变常见于精神紧张、劳累、熬夜、过度吸烟等；'),
    withLine('劳累', '1、生理性改变常见于精神紧张、劳累、熬夜、过度吸烟等；'),
    withLine('熬夜', '1、生理性改变常见于精神紧张、劳累、熬夜、过度吸烟等；'),
    withLine('过度吸烟等', '1、生理性改变常见于精神紧张、劳累、熬夜、过度吸烟等；'),
    withLine('病理性改变常见于高血压', '病理性改变常见于高血压、冠心病、贫血或药物影响等。'),
    withLine('冠心病', '病理性改变常见于高血压、冠心病、贫血或药物影响等。'),
    withLine('贫血或药物影响等', '病理性改变常见于高血压、冠心病、贫血或药物影响等。'),
    { name: '小密低密度脂蛋白(sdLDL)测定', level: 'yellow' as const },
    { name: '高密度脂蛋白胆固醇', level: 'yellow' as const },
  ];
  const { labels, others } = layoutLabels(items);
  assert.equal(labels.length, 0, 'no organ label from explanatory fragments');
  const all = [...labels, ...others].map((x) => x.name);
  for (const frag of ['黄疸', '胆囊结石', '冠心病', '劳累', '熬夜', '过度吸烟等', '病理性改变常见于高血压']) {
    assert.ok(!all.includes(frag), `must be dropped, not shown anywhere: ${frag}`);
  }
  // 真指标(无 origin_line)仍留在底部列表
  assert.ok(others.some((o) => o.name === '高密度脂蛋白胆固醇'));
});

test('layoutLabels keeps a clean conclusion finding even though other entries are dropped', () => {
  const { labels } = layoutLabels([
    { name: '前列腺钙化灶', level: 'yellow', originLine: '2）前列腺钙化灶' },
    { name: '肝左叶中等不均回声', level: 'yellow', originLine: '1、彩超：肝左叶中等不均回声29*28mm，建议：进一步检查' },
  ]);
  const names = labels.map((l) => l.name);
  assert.ok(names.includes('前列腺钙化灶'), 'clean finding kept');
  // 口径: 标题行含「建议：」的真发现会被误漏(可以漏)
  assert.ok(!names.includes('肝左叶中等不均回声'));
});

test('layoutLabels drops non-findings and merges variant duplicates (包雁飞 H003-29)', () => {
  const names = [
    '收缩压', '淋巴细胞比率', '红细胞分布宽度', '肌酐（酶法）', '肾小球滤过率（MDRD）法',
    'CKD-EPI (cre估算)', '肝左叶中等不均回声', '肝左叶局部稍低密度灶', '左肾多发囊肿',
    '前列腺钙化灶', '右耳耵聍栓塞', '炎症感染', '右耳耵聍栓', '肝左叶中等不均回声29*28mm',
    '胸部CT平扫', '诊室血压', '曾经炎症感染', '升高', '降低', '某些药物', '较大块耵聍栓',
  ];
  const { labels, others } = layoutLabels(names.map((name) => ({ name, level: 'yellow' as const })));
  const all = [...labels, ...others].map((x) => x.name);
  for (const bad of ['升高', '降低', '某些药物', '炎症感染', '曾经炎症感染', '胸部CT平扫', '诊室血压']) {
    assert.ok(!all.includes(bad), `noise should be dropped: ${bad}`);
  }
  assert.equal(all.filter((n) => n.includes('耵聍')).length, 1, 'ear variants should merge to one');
  assert.equal(all.filter((n) => n.includes('肝左叶中等')).length, 1, 'liver variants should merge to one');
  // 真发现不再被重复项挤出到下方列表
  assert.ok(labels.some((l) => l.anchorKey === 'pelvis' && l.name.includes('前列腺钙化灶')));
});
