import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from 'antd';
import { MenuOutlined } from '@ant-design/icons';
import Layout from '../components/Layout';
import ChatPanel from '../components/ChatPanel';
import SessionDrawer from '../components/SessionDrawer';
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
        } else {
          api.post('/chat/sessions', {}).then(r2 => {
            store.setCurrentSession(r2.data.id);
          }).catch(() => {});
        }
      })
      .catch(() => {});
  }, [sessionId]);

  return (
    <Layout title="AI 健康咨询">
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)}
          style={{ color: 'var(--color-text-secondary)' }}>
          历史对话
        </Button>
      </div>

      {store.currentSessionId ? (
        <ChatPanel sessionId={store.currentSessionId} />
      ) : (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--color-text-secondary)' }}>
          加载中...
        </div>
      )}

      <SessionDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </Layout>
  );
}
