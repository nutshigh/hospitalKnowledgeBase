import { useNavigate } from 'react-router-dom';
import { List } from 'antd';
import { UserOutlined, BellOutlined, SettingOutlined, LogoutOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';

export default function ProfilePage() {
  const { logout } = useUserStore();
  const nav = useNavigate();

  const items = [
    { icon: <BellOutlined />, title: '消息通知', onClick: () => {} },
    { icon: <SettingOutlined />, title: '设置', onClick: () => {} },
    { icon: <LogoutOutlined />, title: '退出登录', onClick: () => { logout(); nav('/login'); }, danger: true },
  ];

  return (
    <Layout title="个人中心">
      <div style={{ textAlign: 'center', padding: '24px 0 32px' }}>
        <div style={{
          width: 64, height: 64, borderRadius: '50%', background: 'var(--color-primary-light)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 12px',
        }}>
          <UserOutlined style={{ fontSize: 24, color: 'var(--color-primary)' }} />
        </div>
        <div style={{ fontWeight: 600, fontSize: 16 }}>体检用户</div>
      </div>
      <div style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', overflow: 'hidden', boxShadow: 'var(--shadow-sm)' }}>
        {items.map((item, i) => (
          <div
            key={i}
            onClick={item.onClick}
            style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
              cursor: 'pointer', borderBottom: i < items.length - 1 ? '1px solid var(--color-border-light)' : 'none',
              color: item.danger ? 'var(--color-red)' : 'var(--color-text)',
            }}
          >
            <span style={{ fontSize: 16, width: 24 }}>{item.icon}</span>
            <span style={{ fontSize: 14, fontWeight: 500 }}>{item.title}</span>
            <span style={{ marginLeft: 'auto', color: 'var(--color-text-secondary)', fontSize: 16 }}>›</span>
          </div>
        ))}
      </div>
    </Layout>
  );
}
