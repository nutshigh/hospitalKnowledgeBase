import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Spin, Tabs, Button, Empty, Tag, message } from 'antd';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import { useUserStore } from '../stores/userStore';
import { useFollowupStore } from '../stores/followupStore';

interface RecheckItem {
  item_name: string; result_value: string | null; unit: string | null;
  ref_range: string | null; color_level: string;
}

interface FollowupItem {
  id: number; report_id: number; status: 'pending' | 'completed';
  overall_level: string; recheck_indicators: RecheckItem[];
  template_name: string | null; generated_at: string | null; submitted_at: string | null;
}

interface NotifItem {
  id: number; title: string; category: string; is_read: boolean; created_at: string | null;
}

export default function FollowUpCenterPage() {
  const { api } = useUserStore();
  const nav = useNavigate();
  const refreshBadge = useFollowupStore(s => s.refresh);
  const [loading, setLoading] = useState(true);
  const [followups, setFollowups] = useState<FollowupItem[]>([]);
  const [notifs, setNotifs] = useState<NotifItem[]>([]);

  const load = async () => {
    try {
      const [f, n] = await Promise.all([
        api.get('/followup/center', { params: { page: 1, page_size: 50 } }),
        api.get('/notifications', { params: { page: 1, page_size: 50 } }),
      ]);
      setFollowups(f.data.items || []);
      setNotifs(n.data.items || []);
    } catch { /* 空态处理 */ }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); refreshBadge(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const readAll = async () => {
    try {
      await api.post('/notifications/read-all');
      setNotifs(notifs.map(x => ({ ...x, is_read: true })));
      refreshBadge();
      message.success('已全部标记为已读');
    } catch { message.error('操作失败,请重试'); }
  };

  if (loading) return <Layout title="随访"><div style={{ textAlign: 'center', padding: 60 }}><Spin /></div></Layout>;

  const pending = followups.filter(f => f.status === 'pending');
  const done = followups.filter(f => f.status === 'completed');

  const renderFollowup = (f: FollowupItem) => (
    <div key={f.id} style={{
      background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
      padding: '14px 16px', boxShadow: 'var(--shadow-sm)',
      border: '1px solid var(--color-border-light)', marginBottom: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>体检随访</span>
        <ColorBadge level={f.overall_level} size="sm" />
        {f.status === 'completed'
          ? <Tag color="green">已填写</Tag>
          : <Tag color="orange">待填写</Tag>}
        {f.generated_at && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--color-text-secondary)' }}>{f.generated_at.slice(0, 10)}</span>}
      </div>
      {(f.recheck_indicators || []).length > 0 && (
        <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 10 }}>
          {(f.recheck_indicators || []).map((it, i) => (
            <div key={i}>
              {it.item_name}: {it.result_value ?? '-'}{it.unit ? ' ' + it.unit : ''}
              {it.ref_range ? `（参考 ${it.ref_range}）` : ''}
            </div>
          ))}
        </div>
      )}
      {f.status === 'pending' && (
        <Button type="primary" size="small" onClick={() => nav(`/followup/${f.id}`)}>去填写</Button>
      )}
    </div>
  );

  return (
    <Layout title="随访">
      <Tabs
        defaultActiveKey="todo"
        items={[
          {
            key: 'todo', label: `待随访${pending.length ? `(${pending.length})` : ''}`,
            children: pending.length ? pending.map(renderFollowup)
              : <Empty description="暂无待随访问卷" />,
          },
          {
            key: 'done', label: '已填写',
            children: done.length ? done.map(renderFollowup)
              : <Empty description="暂无已填写问卷" />,
          },
          {
            key: 'notify', label: '提醒通知',
            children: (
              <>
                <div style={{ textAlign: 'right', marginBottom: 8 }}>
                  <Button size="small" onClick={readAll}>全部已读</Button>
                </div>
                {notifs.length ? notifs.map(n => (
                  <div key={n.id} style={{
                    display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 12px',
                    borderBottom: '1px solid var(--color-border-light)',
                    background: n.is_read ? 'transparent' : 'var(--color-primary-light)',
                    borderRadius: 'var(--radius-sm)', marginBottom: 6,
                    cursor: 'pointer',
                  }} onClick={async () => {
                    if (!n.is_read) {
                      try {
                        await api.post(`/notifications/${n.id}/read`);
                        refreshBadge();
                        load();
                      } catch { message.error('操作失败,请重试'); }
                    }
                  }}>
                    <span style={{ fontSize: 13 }}>{n.is_read ? '✓' : '●'}</span>
                    <span style={{ fontSize: 13 }}>{n.title}</span>
                  </div>
                )) : <Empty description="暂无通知" />}
              </>
            ),
          },
        ]}
      />
    </Layout>
  );
}
