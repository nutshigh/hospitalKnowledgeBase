import { useEffect, useState } from 'react';
import { Input, Table } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function TrendPage() {
  const { api } = useDoctorStore();
  const [indicator, setIndicator] = useState('空腹血糖（GLU）');
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    if (indicator) api.get(`/statistics/trend?indicator=${encodeURIComponent(indicator)}&years=5`).then(r => setData(r.data));
    // eslint-disable-next-line
  }, [indicator]);

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>趋势分析</h2>
      <Input placeholder="输入指标名称" value={indicator} onChange={e => setIndicator(e.target.value)} style={{ width: 300, marginBottom: 16 }} />
      <Table dataSource={data?.trend || []} rowKey="year" columns={[
        { title: '年份', dataIndex: 'year' }, { title: '总数', dataIndex: 'total' },
        { title: '异常率', dataIndex: 'abnormal_rate', render: (v: number) => `${v}%` },
      ]} style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' }} />
    </DoctorLayout>
  );
}
