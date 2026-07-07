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
  const [indicators, setIndicators] = useState<any[]>([]);

  useEffect(() => {
    api.get(`/reports/${id}`).then(r => { setReport(r.data); });
    api.get(`/interpretations/${id}`).then(r => setInterp(r.data));
  }, [id]);

  useEffect(() => {
    if (!report?.id) return;
    setIndicators(report?.indicators || []);
  }, [report]);

  if (!report) return <DoctorLayout><Spin /></DoctorLayout>;

  const columns = [
    { title: '指标', dataIndex: 'item_name', key: 'item_name' },
    { title: '结果', dataIndex: 'result_value', key: 'result_value',
      render: (v: any, r: any) => <span>{v} <span style={{ color: '#888', fontSize: 12 }}>{r.unit}</span></span> },
    { title: '参考范围', key: 'ref',
      render: (_: any, r: any) => r.ref_range_low && r.ref_range_high ? `${r.ref_range_low}-${r.ref_range_high}` : '-' },
    { title: '色级', dataIndex: 'color_level', key: 'color_level',
      render: (c: string) => c ? <Tag color={COLORS[c]}>{c}</Tag> : '-' },
    { title: '偏离', dataIndex: 'deviation', key: 'deviation',
      render: (d: string) => d ? <span>{DEVIATION_TXT[d] || d}</span> : '-' },
  ];

  const sortedIndicators = [...indicators].sort((a, b) => {
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
        loading={interp?.status && interp.status !== 'completed'}
        qualityNote={interp?.quality_note}
      />
    </DoctorLayout>
  );
}
