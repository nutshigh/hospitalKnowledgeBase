import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect } from 'react';
import { Drawer, List, Button, Typography, Popconfirm } from 'antd';
import { PlusOutlined, DeleteOutlined, MessageOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';
export default function SessionDrawer({ open, onClose }) {
    const { api } = useUserStore();
    const store = useChatStore();
    useEffect(() => {
        if (!open)
            return;
        api.get('/chat/sessions')
            .then(r => store.setSessions(r.data || []))
            .catch(() => { });
    }, [open]);
    const handleNew = async () => {
        try {
            const r = await api.post('/chat/sessions', {});
            store.setCurrentSession(r.data.id);
            store.setMessages([]);
            onClose();
        }
        catch { }
    };
    const handleSelect = (id) => {
        store.setCurrentSession(id);
        onClose();
    };
    const handleDelete = async (id) => {
        try {
            await api.delete(`/chat/sessions/${id}`);
            store.setSessions(store.sessions.filter(s => s.id !== id));
            if (store.currentSessionId === id) {
                store.setCurrentSession(null);
                store.setMessages([]);
            }
        }
        catch { }
    };
    return (_jsxs(Drawer, { title: "\u5BF9\u8BDD\u5386\u53F2", open: open, onClose: onClose, width: 280, children: [_jsx(Button, { type: "primary", icon: _jsx(PlusOutlined, {}), block: true, onClick: handleNew, style: { marginBottom: 16, borderRadius: 8 }, children: "\u65B0\u5BF9\u8BDD" }), _jsx(List, { dataSource: store.sessions, renderItem: (session) => (_jsx(List.Item, { onClick: () => handleSelect(session.id), style: {
                        cursor: 'pointer', borderRadius: 8, padding: '8px 12px',
                        background: session.id === store.currentSessionId ? '#F0FDFA' : undefined,
                    }, actions: [
                        _jsx(Popconfirm, { title: "\u786E\u5B9A\u5220\u9664\uFF1F", onConfirm: (e) => { e?.stopPropagation(); handleDelete(session.id); }, children: _jsx(DeleteOutlined, { onClick: (e) => e?.stopPropagation(), style: { color: '#EF4444', fontSize: 12 } }) })
                    ], children: _jsx(List.Item.Meta, { avatar: _jsx(MessageOutlined, { style: { color: '#0D9488' } }), title: _jsx(Typography.Text, { ellipsis: true, style: { fontSize: 13 }, children: session.title || '新对话' }), description: _jsx(Typography.Text, { type: "secondary", style: { fontSize: 11 }, children: session.updated_at?.slice(0, 16) }) }) })) })] }));
}
