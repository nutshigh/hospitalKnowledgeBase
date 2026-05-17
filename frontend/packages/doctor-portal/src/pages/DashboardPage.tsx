import { useEffect, useState } from 'react';
import { Card, Spin } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function DashboardPage() {
  const { api } = useDoctorStore();
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    api.get('/statistics/dashboard?start_date=2024-01-01&end_date=2026-12-31').then(r => setData(r.data)).catch(() => {});
  }, []);

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 24 }}>工作台概览</h2>
      {!data ? <Spin /> : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
          {[
            { label: '报告总数', value: data.total_reports, color: 'var(--color-primary)' },
            { label: '红区报告', value: data.red_reports, color: 'var(--color-red)' },
            { label: '黄区报告', value: data.yellow_reports, color: 'var(--color-yellow)' },
            { label: '异常率', value: data.abnormal_rate + '%', color: 'var(--color-text)' },
          ].map(s => (
            <Card key={s.label} style={{ textAlign: 'center', border: '1px solid var(--color-border)' }}>
              <div style={{ fontSize: 28, fontWeight: 700, color: s.color }}>{s.value ?? '-'}</div>
              <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginTop: 4 }}>{s.label}</div>
            </Card>
          ))}
        </div>
      )}
    </DoctorLayout>
  );
}
