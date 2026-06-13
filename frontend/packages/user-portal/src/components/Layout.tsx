import { ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { HomeOutlined, MessageOutlined, UserOutlined } from '@ant-design/icons';

const tabs = [
  { key: '/', label: '首页', icon: <HomeOutlined /> },
  { key: '/chat', label: 'AI咨询', icon: <MessageOutlined /> },
  { key: '/profile', label: '我的', icon: <UserOutlined /> },
];

export default function Layout({ children, title }: { children: ReactNode; title?: string }) {
  const nav = useNavigate();
  const loc = useLocation();

  const isActive = (key: string) => {
    if (key === '/') return loc.pathname === '/';
    return loc.pathname.startsWith(key);
  };

  return (
    <div style={{ maxWidth: 480, margin: '0 auto', minHeight: '100vh', background: 'var(--color-surface)' }}>
      <header style={{
        padding: '20px 24px', borderBottom: '1px solid var(--color-border-light)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <h2 style={{ fontSize: 20, fontWeight: 700 }}>{title || '体检报告'}</h2>
      </header>
      <main style={{ padding: '16px 20px 80px' }}>
        {children}
      </main>
      <nav style={{
        position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)',
        width: '100%', maxWidth: 480,
        display: 'flex', justifyContent: 'space-around', alignItems: 'center',
        background: 'var(--color-surface)', borderTop: '1px solid var(--color-border-light)',
        padding: '8px 0 env(safe-area-inset-bottom, 8px)',
        zIndex: 100,
      }}>
        {tabs.map(t => (
          <div
            key={t.key}
            onClick={() => nav(t.key)}
            style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
              padding: '4px 24px', cursor: 'pointer',
              color: isActive(t.key) ? 'var(--color-primary)' : 'var(--color-text-secondary)',
              fontSize: 12, fontWeight: isActive(t.key) ? 600 : 400,
              transition: 'color 0.2s',
              userSelect: 'none',
            }}
          >
            <span style={{ fontSize: 20 }}>{t.icon}</span>
            <span>{t.label}</span>
          </div>
        ))}
      </nav>
    </div>
  );
}
