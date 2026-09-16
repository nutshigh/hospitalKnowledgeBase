import { useEffect, useState, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { Spin, Card, Tag, Table } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
import { InterpretationReportCard } from '@hospital/shared';
import FollowupPanel from '../components/FollowupPanel';

const COLORS: any = { red: 'red', yellow: 'gold', green: 'green' };
const DEVIATION_TXT: any = { high: '↑ 偏高', low: '↓ 偏低', normal: '正常' };

// 2026-09-15(口径确认): 计数 = 指标逐条 + **总检建议整体计 1**(红优先，否则黄)，
// 不把总检建议里的异常逐条拆分
function computeLevelCounts(indicators: any[]) {
  const c = { red: 0, yellow: 0, green: 0 };
  const concl: any[] = [];
  for (const it of indicators || []) {
    if (it.source === 'conclusion') { concl.push(it); continue; }
    const l: string = it.color_level;
    if (l === 'red' || l === 'yellow' || l === 'green') c[l] += 1;
  }
  if (concl.some((it: any) => it.color_level === 'red')) c.red += 1;
  else if (concl.some((it: any) => it.color_level === 'yellow')) c.yellow += 1;
  return c;
}

// 2026-09-14: 结论段渲染 —— 发现标题行(科普/建议正文前的标题)加粗, 建议/解释正文保持
// 原样; 红区命中句标红(优先)。标题判定: 非编号/非建议起句、≤30字、无句末标点;
// "【…】"开头行只加粗【…】部分。
const TITLE_ADVICE_RE = /^(建议|请|可见于|多见于|可能|是指|常见|可导致|日常|考虑|属于|若|注意|定期|避免|必要时|根据|通常|此征象|生活|多食|少食|控制|加强|出现|如)/;
const TITLE_NUM_RE = /^[（(]?\d{1,3}\s*[、.:：)）]/;

function isFindingsTitle(line: string): boolean {
  const t = line.trim();
  if (!t) return false;
  // 编号+冒号结尾的长标题(可含逗号/顿号分隔的多个异常, 如"2. 血压增高…彩超提示左房大:")
  // 加粗; 其余编号行与普通行走同一通用分支(短、无句末标点 → 视为发现标题)
  if (TITLE_NUM_RE.test(t) && /[:：]\s*$/.test(t)) return true;
  // 2026-09-16: 编号发现行(可含多名列表, 常超 40 字, 如"1:胃镜 慢性萎缩性胃炎（C1）…")
  // 也加粗 —— 原 40 字上限把这些行漏掉(广西人民)
  if (TITLE_NUM_RE.test(t) && !TITLE_ADVICE_RE.test(t) && !/[。！？；;]$/.test(t)) return true;
  if (t.length > 40) return false;
  if (TITLE_ADVICE_RE.test(t)) return false;
  if (/[。！？；;]$/.test(t)) return false;
  return true;
}

function renderConclusionText(text: string, redSentences: string[]) {
  const reds = (redSentences || []).filter((h) => h && text.includes(h))
    .sort((a, b) => b.length - a.length);
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const redRe = reds.length ? new RegExp(`(${reds.map(esc).join('|')})`, 'g') : null;
  const inline = (s: string): ReactNode => {
    if (!redRe) return s;
    return s.split(redRe).map((p, i) =>
      reds.includes(p)
        ? <span key={i} style={{ color: '#DC2626', fontWeight: 600 }}>{p}</span>
        : <span key={i}>{p}</span>
    );
  };
  return text.split('\n').map((line, i) => {
    const t = line.trim();
    if (t.startsWith('【')) {
      const m = t.match(/^(【[^】]{1,20}】)([\s\S]*)$/);
      return (
        <div key={i}>
          <span style={{ fontWeight: 700 }}>{m ? m[1] : t}</span>
          {m ? inline(m[2]) : null}
        </div>
      );
    }
    if (isFindingsTitle(t)) {
      return <div key={i} style={{ fontWeight: 700 }}>{inline(line)}</div>;
    }
    return <div key={i}>{inline(line)}</div>;
  });
}

export default function ReportDetailPage() {
  const { id } = useParams();
  const { api } = useDoctorStore();
  const [report, setReport] = useState<any>(null);
  const [interp, setInterp] = useState<any>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = () => {
      Promise.all([
        api.get(`/reports/${id}`).catch(() => ({ data: null })),
        api.get(`/interpretations/${id}`).catch(() => ({ data: null })),
      ]).then(([r, i]) => {
        setReport(r.data);
        setInterp(i.data);
        const interpDone = i.data?.status === 'completed' || i.data?.status === 'failed';
        const taskDone = !r.data?.task_status || r.data.task_status === 'completed' || r.data.task_status === 'failed';
        if (!taskDone || !interpDone) {
          timer = setTimeout(poll, 10000);
        }
      });
    };
    poll();
    return () => { if (timer) clearTimeout(timer); };
  }, [id]);

  if (!report) return <DoctorLayout><Spin /></DoctorLayout>;

  const columns = [
    { title: '指标', dataIndex: 'item_name', key: 'item_name',
      // 2026-09-09: 总检异常条目映射回总检建议段原文行(后端 origin_line)
      render: (v: any, r: any) => r?.source === 'conclusion' && r.origin_line ? (
        <div>{v}<div style={{ color: '#999', fontSize: 12, fontWeight: 'normal' }}>原文: {r.origin_line}</div></div>
      ) : v },
    { title: '结果', dataIndex: 'result_value', key: 'result_value',
      render: (v: any, r: any) => <span>{v} <span style={{ color: '#888', fontSize: 12 }}>{r.unit}</span></span> },
    { title: '参考范围', key: 'ref',
      // 2026-08-31: 单限参考范围也显示(<5.2 / >1.04), "无"视为无值
      render: (_: any, r: any) => {
        const ok = (v: any) => v !== undefined && v !== null && v !== '' && v !== '无';
        if (ok(r.ref_range_low) && ok(r.ref_range_high)) return `${r.ref_range_low}-${r.ref_range_high}`;
        if (ok(r.ref_range_high)) return `<${r.ref_range_high}`;
        if (ok(r.ref_range_low)) return `>${r.ref_range_low}`;
        return '-';
      } },
    { title: '色级', dataIndex: 'color_level', key: 'color_level',
      render: (c: string) => c ? <Tag color={COLORS[c]}>{c}</Tag> : '-' },
    { title: '偏离', dataIndex: 'deviation', key: 'deviation',
      render: (d: string) => d ? <span>{DEVIATION_TXT[d] || d}</span> : '-' },
  ];

  const rawIndicators = interp?.indicators?.length
    ? interp.indicators
    : (report?.indicators || []);
  const sortedIndicators = [...rawIndicators].sort((a, b) => {
    const order: any = { red: 0, yellow: 1, green: 2 };
    return (order[a.color_level] ?? 3) - (order[b.color_level] ?? 3);
  });
  const redConclusionSentences = (interp?.indicators || [])
    .filter((it: any) => it.color_level === 'red' && it.origin_line)
    .map((it: any) => it.origin_line as string);
  const levelCounts = computeLevelCounts(interp?.indicators || []);


  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>{report.name || '报告详情'}</h2>
      <Card style={{ marginBottom: 16 }}>
        <p>性别: {report.gender} · 年龄: {report.age} · 日期: {report.report_date}</p>
        {report.unit_name && <p>单位: {report.unit_name}</p>}
      </Card>

      <Card title={<span style={{ fontSize: 18, color: '#000' }}>总检建议与结论（包含影像类结果）</span>} style={{ marginBottom: 16 }}>
        {report.conclusion_text ? (
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>
            {renderConclusionText(report.conclusion_text, redConclusionSentences)}
          </div>
        ) : (
          <div style={{ color: '#999', fontStyle: 'italic' }}>未提取到结论</div>
        )}
      </Card>

      {interp && (
        <div style={{ marginBottom: 12 }}>
          <Tag color="red">红区 {levelCounts.red}</Tag>
          <Tag color="gold">黄区 {levelCounts.yellow}</Tag>
          <Tag color="green">绿区 {levelCounts.green}</Tag>
          <span style={{ marginLeft: 8 }}>
            整体判定：
            <Tag color={COLORS[interp.overall_level]}>{interp.overall_level}</Tag>
          </span>
        </div>
      )}

      <Card title="指标明细" style={{ marginBottom: 16 }}>
        <Table
          columns={columns}
          dataSource={sortedIndicators.map((i: any, idx: number) => ({ ...i, key: idx }))}
          pagination={{ pageSize: 20 }}
          size="small"
        />
      </Card>

      <InterpretationReportCard
        summaries={interp?.summaries}
        references={interp?.references}
        loading={!interp || interp.status !== 'completed'}
        qualityNote={interp?.quality_note}
      />
      {interp?.status === 'completed' && <FollowupPanel reportId={Number(id)} />}
    </DoctorLayout>
  );
}
