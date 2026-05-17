import { useState } from 'react';
import { Card, Table, Button, Modal, Input, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useAdminStore } from '../stores/adminStore';

export default function PlatformDashboard() {
  const { api } = useAdminStore();
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ hospital_id: '', hospital_name: '' });
  const [hospitals, setHospitals] = useState<any[]>([{ hospital_id: 'H001', hospital_name: '示例医院', is_active: 1, created_at: '2025-01-01' }]);

  const handleAdd = async () => {
    try {
      await api.post('/auth/register', { username: form.hospital_id + '_admin', password: '123456', role: 'doctor', hospital_id: form.hospital_id });
      setHospitals([...hospitals, { hospital_id: form.hospital_id, hospital_name: form.hospital_name, is_active: 1, created_at: new Date().toISOString() }]);
      message.success('医院已接入'); setShowAdd(false);
    } catch { message.error('接入失败'); }
  };

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 24 }}>
        <h2>平台管理后台</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setShowAdd(true)}>接入新医院</Button>
      </div>
      <Card title="已接入医院">
        <Table dataSource={hospitals} rowKey="hospital_id" columns={[
          { title: '医院ID', dataIndex: 'hospital_id' },
          { title: '医院名称', dataIndex: 'hospital_name' },
          { title: '状态', dataIndex: 'is_active', render: (v: number) => v ? '✅ 正常' : '⛔ 已禁用' },
          { title: '接入时间', dataIndex: 'created_at' },
        ]} />
      </Card>
      <Modal title="接入新医院" open={showAdd} onOk={handleAdd} onCancel={() => setShowAdd(false)}>
        <Input placeholder="医院ID (如 H002)" value={form.hospital_id} onChange={e => setForm({ ...form, hospital_id: e.target.value })} style={{ marginBottom: 12 }} />
        <Input placeholder="医院名称" value={form.hospital_name} onChange={e => setForm({ ...form, hospital_name: e.target.value })} />
      </Modal>
    </div>
  );
}
