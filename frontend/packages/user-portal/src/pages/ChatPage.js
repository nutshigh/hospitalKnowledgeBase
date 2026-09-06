import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from 'antd';
import { MenuOutlined } from '@ant-design/icons';
import Layout from '../components/Layout';
import ChatPanel from '../components/ChatPanel';
import SessionDrawer from '../components/SessionDrawer';
import ReportSelector from '../components/ReportSelector';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';
export default function ChatPage() {
    const { sessionId } = useParams();
    const { api } = useUserStore();
    const store = useChatStore();
    const [drawerOpen, setDrawerOpen] = useState(false);
    useEffect(() => {
        if (sessionId) {
            store.setCurrentSession(Number(sessionId));
            return;
        }
        // Load latest session or create a new one
        api.get('/chat/sessions')
            .then(r => {
            const sessions = r.data || [];
            if (sessions.length > 0) {
                store.setCurrentSession(sessions[0].id);
                store.setSessions(sessions);
            }
            else {
                api.post('/chat/sessions', {}).then(r2 => {
                    store.setCurrentSession(r2.data.id);
                }).catch(() => { });
            }
        })
            .catch(() => { });
    }, [sessionId]);
    return (_jsxs(Layout, { title: "AI \u5065\u5EB7\u54A8\u8BE2", children: [_jsxs("div", { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }, children: [store.currentSessionId && _jsx(ReportSelector, { sessionId: store.currentSessionId }), _jsx(Button, { type: "text", icon: _jsx(MenuOutlined, {}), onClick: () => setDrawerOpen(true), style: { color: 'var(--color-text-secondary)', flexShrink: 0 }, children: "\u5386\u53F2\u5BF9\u8BDD" })] }), store.currentSessionId ? (_jsx(ChatPanel, { sessionId: store.currentSessionId })) : (_jsx("div", { style: { textAlign: 'center', padding: 60, color: 'var(--color-text-secondary)' }, children: "\u52A0\u8F7D\u4E2D..." })), _jsx(SessionDrawer, { open: drawerOpen, onClose: () => setDrawerOpen(false) })] }));
}
