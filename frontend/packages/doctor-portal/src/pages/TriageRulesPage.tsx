import { useEffect, useState } from 'react';
import { Table, Button, Modal, Input, Select, Switch, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function TriageRulesPage() {
  const { api } = useDoctorStore();
  const [rules, setRules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState({ rule_name: '', rule_type: 'value_range', indicator_code: '', conditions: '{}', color_level: 'yellow', priority: 0 });

  const load = () => {
    api.get('/interpretations/rules/all').then(r => setRules(r.data || [])).finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    try {
      await api.post('/interpretations/rules', { ...form, conditions: JSON.parse(form.conditions) });
      message.success('创建成功');
      setShowModal(false);
      load();
    } catch { message.error('请检查条件 JSON 格式'); }
  };

  const handleToggle = async (id: number, active: boolean) => {
    await api.put(`/interpretations/rules/${id}`, { is_active: active ? 1 : 0 });
    load();
  };

  const columns = [
    { title: '规则名', dataIndex: 'rule_name', key: 'rule_name' },
    { title: '类型', dataIndex: 'rule_type', key: 'rule_type', width: 100 },
    { title: '等级', dataIndex: 'color_level', key: 'color_level', width: 80, render: (v: string) => (
      <span style={{ color: v === 'red' ? 'var(--color-red)' : v === 'yellow' ? 'var(--color-yellow)' : 'var(--color-green)', fontWeight: 600 }}>{v}</span>
    )},
    { title: '启用', key: 'active', width: 80, render: (_: any, r: any) => (
      <Switch checked={!!r.is_active} onChange={(v) => handleToggle(r.id, v)} />
    )},
    { title: '操作', key: 'action', width: 80, render: (_: any, r: any) => (
      <Button size="small" danger onClick={async () => { await api.delete(`/interpretations/rules/${r.id}`); load(); }}>删除</Button>
    )},
  ];

  return (
    <DoctorLayout>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>三色规则配置</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setShowModal(true)}>新建规则</Button>
      </div>
      <Table dataSource={rules} columns={columns} loading={loading} rowKey="id"
        style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' }} />
      <Modal title="新建规则" open={showModal} onOk={handleCreate} onCancel={() => setShowModal(false)}>
        <Input placeholder="规则名称" value={form.rule_name} onChange={e => setForm({ ...form, rule_name: e.target.value })} style={{ marginBottom: 12 }} />
        <Select value={form.rule_type} onChange={(v) => setForm({ ...form, rule_type: v })}
          options={[{ value: 'value_range', label: '数值范围' }, { value: 'key_indicator', label: '关键指标' }, { value: 'combo', label: '组合规则' }, { value: 'trend', label: '趋势规则' }]}
          style={{ width: '100%', marginBottom: 12 }} />
        <Input placeholder="指标编码 (可选)" value={form.indicator_code} onChange={e => setForm({ ...form, indicator_code: e.target.value })} style={{ marginBottom: 12 }} />
        <Input.TextArea placeholder="条件 JSON" value={form.conditions} onChange={e => setForm({ ...form, conditions: e.target.value })} rows={4} style={{ marginBottom: 12 }} />
        <Select value={form.color_level} onChange={(v) => setForm({ ...form, color_level: v })}
          options={[{ value: 'red', label: '红区' }, { value: 'yellow', label: '黄区' }, { value: 'green', label: '绿区' }]}
          style={{ width: '100%' }} />
      </Modal>
    </DoctorLayout>
  );
}
