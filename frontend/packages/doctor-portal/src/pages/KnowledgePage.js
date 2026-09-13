import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Table, Button, Modal, Input, Upload, message, Select } from 'antd';
import { PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
export default function KnowledgePage() {
    const { api } = useDoctorStore();
    const [entries, setEntries] = useState([]);
    const [categories, setCategories] = useState([]);
    const [loading, setLoading] = useState(true);
    const [showModal, setShowModal] = useState(false);
    const [form, setForm] = useState({ title: '', content: '', category_id: undefined });
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
    const handleImport = async (file) => {
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
        { title: '操作', key: 'action', width: 80, render: (_, r) => (_jsx(Button, { size: "small", danger: true, onClick: async () => { await api.delete(`/knowledge/entries/${r.id}`); load(); }, children: "\u5220\u9664" })) },
    ];
    return (_jsxs(DoctorLayout, { children: [_jsxs("div", { style: { display: 'flex', justifyContent: 'space-between', marginBottom: 16 }, children: [_jsx("h2", { children: "\u77E5\u8BC6\u5E93\u7BA1\u7406" }), _jsxs("div", { style: { display: 'flex', gap: 8 }, children: [_jsx(Upload, { beforeUpload: handleImport, showUploadList: false, children: _jsx(Button, { icon: _jsx(UploadOutlined, {}), children: "\u5BFC\u5165\u6587\u6863" }) }), _jsx(Button, { type: "primary", icon: _jsx(PlusOutlined, {}), onClick: () => setShowModal(true), children: "\u65B0\u5EFA\u6761\u76EE" })] })] }), _jsx(Table, { dataSource: entries, columns: columns, loading: loading, rowKey: "id", style: { background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' } }), _jsxs(Modal, { title: "\u65B0\u5EFA\u77E5\u8BC6\u6761\u76EE", open: showModal, onOk: handleCreate, onCancel: () => setShowModal(false), children: [_jsx(Input, { placeholder: "\u6807\u9898", value: form.title, onChange: e => setForm({ ...form, title: e.target.value }), style: { marginBottom: 12 } }), _jsx(Select, { placeholder: "\u5206\u7C7B", value: form.category_id, onChange: (v) => setForm({ ...form, category_id: v }), options: categories.map((c) => ({ value: c.id, label: c.name })), style: { width: '100%', marginBottom: 12 } }), _jsx(Input.TextArea, { placeholder: "\u5185\u5BB9", value: form.content, onChange: e => setForm({ ...form, content: e.target.value }), rows: 6 })] })] }));
}
