import { useEffect } from 'react';
import { Drawer, List, Button, Typography, Popconfirm } from 'antd';
import { PlusOutlined, DeleteOutlined, MessageOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function SessionDrawer({ open, onClose }: Props) {
  const { api } = useUserStore();
  const store = useChatStore();

  useEffect(() => {
    if (!open) return;
    api.get('/chat/sessions')
      .then(r => store.setSessions(r.data || []))
      .catch(() => {});
  }, [open]);

  const handleNew = async () => {
    try {
      const r = await api.post('/chat/sessions', {});
      store.setCurrentSession(r.data.id);
      store.setMessages([]);
      onClose();
    } catch {}
  };

  const handleSelect = (id: number) => {
    store.setCurrentSession(id);
    onClose();
  };

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/chat/sessions/${id}`);
      store.setSessions(store.sessions.filter(s => s.id !== id));
      if (store.currentSessionId === id) {
        store.setCurrentSession(null);
        store.setMessages([]);
      }
    } catch {}
  };

  return (
    <Drawer title="对话历史" open={open} onClose={onClose} width={280}>
      <Button type="primary" icon={<PlusOutlined />} block onClick={handleNew}
        style={{ marginBottom: 16, borderRadius: 8 }}>
        新对话
      </Button>
      <List
        dataSource={store.sessions}
        renderItem={(session) => (
          <List.Item
            onClick={() => handleSelect(session.id)}
            style={{
              cursor: 'pointer', borderRadius: 8, padding: '8px 12px',
              background: session.id === store.currentSessionId ? '#F0FDFA' : undefined,
            }}
            actions={[
              <Popconfirm title="确定删除？" onConfirm={(e) => { e?.stopPropagation(); handleDelete(session.id); }}>
                <DeleteOutlined onClick={(e) => e?.stopPropagation()}
                  style={{ color: '#EF4444', fontSize: 12 }} />
              </Popconfirm>
            ]}
          >
            <List.Item.Meta
              avatar={<MessageOutlined style={{ color: '#0D9488' }} />}
              title={<Typography.Text ellipsis style={{ fontSize: 13 }}>{session.title || '新对话'}</Typography.Text>}
              description={<Typography.Text type="secondary" style={{ fontSize: 11 }}>{session.updated_at?.slice(0, 16)}</Typography.Text>}
            />
          </List.Item>
        )}
      />
    </Drawer>
  );
}
