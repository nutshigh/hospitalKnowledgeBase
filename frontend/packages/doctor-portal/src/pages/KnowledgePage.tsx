import { useEffect, useState } from 'react';
import { Table, Button, Modal, Input, Upload, message, Select } from 'antd';
import { PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function KnowledgePage() {
  const { api } = useDoctorStore();
  const [entries, setEntries] = useState<any[]>([]);
  const [categories, setCategories] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState({ title: '', content: '', category_id: undefined as number | undefined });

  const load = () => {
    api.get('/knowledge/entries').then(r => setEntries(r.data.items || [])).finally(() => setLoading(false));
    api.get('/knowledge/categories').then(r => setCategories(r.data || []));
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    await api.post('/knowledge/entries', form);
    message.success('创建成功');
    setShowModal(false);
    setForm({ title: '', content: '', category_id: undefined });
    load();
  };

  const handleImport = async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    await api.post('/knowledge/import', fd);
    message.success('导入成功');
    load();
    return false;
  };

  const columns = [
    { title: '标题', dataIndex: 'title', key: 'title' },
    { title: '分类', dataIndex: 'category_id', key: 'category_id', width: 100 },
    { title: '来源', dataIndex: 'source_type', key: 'source_type', width: 80 },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 180 },
    { title: '操作', key: 'action', width: 80, render: (_: any, r: any) => (
      <Button size="small" danger onClick={async () => { await api.delete(`/knowledge/entries/${r.id}`); load(); }}>删除</Button>
    )},
  ];

  return (
    <DoctorLayout>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>知识库管理</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <Upload beforeUpload={handleImport} showUploadList={false}>
            <Button icon={<UploadOutlined />}>导入文档</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setShowModal(true)}>新建条目</Button>
        </div>
      </div>
      <Table dataSource={entries} columns={columns} loading={loading} rowKey="id"
        style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' }} />
      <Modal title="新建知识条目" open={showModal} onOk={handleCreate} onCancel={() => setShowModal(false)}>
        <Input placeholder="标题" value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} style={{ marginBottom: 12 }} />
        <Select placeholder="分类" value={form.category_id} onChange={(v) => setForm({ ...form, category_id: v })}
          options={categories.map((c: any) => ({ value: c.id, label: c.name }))} style={{ width: '100%', marginBottom: 12 }} />
        <Input.TextArea placeholder="内容" value={form.content} onChange={e => setForm({ ...form, content: e.target.value })} rows={6} />
      </Modal>
    </DoctorLayout>
  );
}
