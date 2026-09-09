import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Spin, Card, Tag, Table } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
import { InterpretationReportCard } from '@hospital/shared';

const COLORS: any = { red: 'red', yellow: 'gold', green: 'green' };
const DEVIATION_TXT: any = { high: '↑ 偏高', low: '↓ 偏低', normal: '正常' };

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

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>{report.name || '报告详情'}</h2>
      <Card style={{ marginBottom: 16 }}>
        <p>性别: {report.gender} · 年龄: {report.age} · 日期: {report.report_date}</p>
        {report.unit_name && <p>单位: {report.unit_name}</p>}
      </Card>

      <Card title="总检建议与结论" style={{ marginBottom: 16 }}>
        {report.conclusion_text ? (
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>{report.conclusion_text}</div>
        ) : (
          <div style={{ color: '#999', fontStyle: 'italic' }}>未提取到结论</div>
        )}
      </Card>

      {interp && (
        <div style={{ marginBottom: 12 }}>
          <Tag color="red">红区 {interp.red_count}</Tag>
          <Tag color="gold">黄区 {interp.yellow_count}</Tag>
          <Tag color="green">绿区 {interp.green_count}</Tag>
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
    </DoctorLayout>
  );
}
