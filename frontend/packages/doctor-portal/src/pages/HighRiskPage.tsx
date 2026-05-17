import { useEffect, useState } from 'react';
import { Table, Button, message } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function HighRiskPage() {
  const { api } = useDoctorStore();
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/interpretations/high-risk/list').then(r => setData(r.data.items || [])).finally(() => setLoading(false));
    // eslint-disable-next-line
  }, []);

  const columns = [
    { title: '姓名', dataIndex: 'name', key: 'name' },
    { title: '单位', dataIndex: 'unit_name', key: 'unit_name' },
    { title: '红区指标数', dataIndex: 'red_count', key: 'red_count', render: (v: number) => <span style={{ color: 'var(--color-red)', fontWeight: 700 }}>{v}</span> },
    { title: '日期', dataIndex: 'report_date', key: 'report_date' },
    { title: '操作', key: 'action', render: (_: any, r: any) => (
      <Button size="small" type="primary" onClick={() => message.info('复查通知已下发')}>下发复查通知</Button>
    )},
  ];

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>高风险人群看板 🚨</h2>
      <Table dataSource={data} columns={columns} loading={loading} rowKey="report_id"
        style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' }} />
    </DoctorLayout>
  );
}
