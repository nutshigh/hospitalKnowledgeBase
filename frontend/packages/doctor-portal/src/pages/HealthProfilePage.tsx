import { useEffect, useState } from 'react';
import { DatePicker, Select, Card } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function HealthProfilePage() {
  const { api } = useDoctorStore();
  const [data, setData] = useState<any>(null);
  const [dates, setDates] = useState<[string, string]>(['2024-01-01', '2026-12-31']);

  useEffect(() => {
    api.get(`/statistics/health-profile?start_date=${dates[0]}&end_date=${dates[1]}`).then(r => setData(r.data));
    // eslint-disable-next-line
  }, [dates]);

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>健康画像</h2>
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        <DatePicker.RangePicker onChange={(_, s) => s && setDates([s[0], s[1]])} />
      </div>
      {data?.top_diseases?.map((d: any, i: number) => (
        <Card key={i} size="small" style={{ marginBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>{d.item_name}</span>
            <span style={{ color: d.color_level === 'red' ? 'var(--color-red)' : 'var(--color-yellow)', fontWeight: 600 }}>
              {d.color_level} — {d.count}例
            </span>
          </div>
        </Card>
      ))}
    </DoctorLayout>
  );
}
