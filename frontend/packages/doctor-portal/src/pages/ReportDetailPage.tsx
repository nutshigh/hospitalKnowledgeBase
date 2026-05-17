import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Spin, Card, Tag, Button } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function ReportDetailPage() {
  const { id } = useParams();
  const { api } = useDoctorStore();
  const [report, setReport] = useState<any>(null);
  const [interp, setInterp] = useState<any>(null);

  useEffect(() => {
    api.get(`/reports/${id}`).then(r => setReport(r.data));
    api.get(`/interpretations/${id}`).then(r => setInterp(r.data));
    // eslint-disable-next-line
  }, [id]);

  if (!report) return <DoctorLayout><Spin /></DoctorLayout>;

  const colors: any = { red: 'red', yellow: 'gold', green: 'green' };

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>{report.name || '报告详情'}</h2>
      <Card style={{ marginBottom: 16 }}>
        <p>性别: {report.gender} · 年龄: {report.age} · 日期: {report.report_date}</p>
        {report.unit_name && <p>单位: {report.unit_name}</p>}
      </Card>
      {interp && (
        <Card title={`解读结果 — ${interp.overall_level === 'red' ? '红区' : interp.overall_level === 'yellow' ? '黄区' : '绿区'}`}>
          <div style={{ marginBottom: 12 }}>
            <Tag color="red">红区 {interp.red_count}</Tag>
            <Tag color="gold">黄区 {interp.yellow_count}</Tag>
            <Tag color="green">绿区 {interp.green_count}</Tag>
          </div>
          {interp.indicators?.map((ind: any, i: number) => (
            <Card key={i} size="small" style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <strong>{ind.item_name}</strong>
                <div>
                  <span style={{ marginRight: 12, fontSize: 16, fontWeight: 700 }}>{ind.result_value}</span>
                  <Tag color={colors[ind.color_level]}>{ind.color_level}</Tag>
                  <Tag>{ind.deviation}</Tag>
                </div>
              </div>
              {ind.explanation && <p style={{ fontSize: 13, color: '#666', marginTop: 8 }}>{ind.explanation}</p>}
            </Card>
          ))}
          {interp.summary_text && (
            <div style={{ marginTop: 16, padding: 16, background: '#f9f9f9', borderRadius: 8 }}>
              <strong>综合小结：</strong>
              <p style={{ marginTop: 4 }}>{interp.summary_text}</p>
            </div>
          )}
        </Card>
      )}
    </DoctorLayout>
  );
}
