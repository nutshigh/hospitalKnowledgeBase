import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, Input, DatePicker, Select } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function ReportsPage() {
  const { api } = useDoctorStore();
  const nav = useNavigate();
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/reports').then(r => setData(r.data.items || [])).finally(() => setLoading(false));
    // eslint-disable-next-line
  }, []);

  const columns = [
    { title: '姓名', dataIndex: 'name', key: 'name' },
    { title: '性别', dataIndex: 'gender', key: 'gender', width: 60 },
    { title: '年龄', dataIndex: 'age', key: 'age', width: 60 },
    { title: '单位', dataIndex: 'unit_name', key: 'unit_name' },
    { title: '日期', dataIndex: 'report_date', key: 'report_date' },
  ];

  return (
    <DoctorLayout>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>报告管理</h2>
      </div>
      <Table
        dataSource={data} columns={columns} loading={loading} rowKey="id"
        onRow={(r) => ({ onClick: () => nav(`/reports/${r.id}`), style: { cursor: 'pointer' } })}
        style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' }}
        pagination={{ pageSize: 20 }}
      />
    </DoctorLayout>
  );
}
