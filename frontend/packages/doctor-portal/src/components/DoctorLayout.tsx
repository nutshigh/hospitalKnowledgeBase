import { ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useDoctorStore } from '../stores/doctorStore';

const MENU_BASE = [
  { key: '/', label: '工作台', icon: '📊' },
  { key: '/reports', label: '报告管理', icon: '📋' },
  { key: '/high-risk', label: '高风险人群', icon: '🚨' },
  { key: '/knowledge', label: '知识库管理', icon: '📚' },
  { key: '/triage-rules', label: '三色规则配置', icon: '🎯' },
  { key: '-', label: '—', icon: '' },
  { key: '/statistics/health-profile', label: '健康画像', icon: '📈' },
  { key: '/statistics/cross-compare', label: '多维对比', icon: '🔄' },
  { key: '/statistics/trend', label: '趋势分析', icon: '📉' },
  { key: '/statistics/export', label: '报表导出', icon: '📄' },
  { key: '-', label: '—', icon: '' },
  { key: '/dispatch', label: '调度管理', icon: '⚙️' },
];

const ADMIN_MENU = [
  { key: '/batch', label: '批量上传分发', icon: '📦' },
];

export default function DoctorLayout({ children }: { children: ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const { logout, sidebarCollapsed, toggleSidebar, role } = useDoctorStore();
  const MENU = role === 'admin' ? [...MENU_BASE.slice(0, MENU_BASE.length - 1), ...ADMIN_MENU, MENU_BASE[MENU_BASE.length - 1]] : MENU_BASE;

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      {/* Sidebar */}
      <aside style={{
        width: sidebarCollapsed ? 56 : 'var(--sidebar-w)', background: 'var(--color-surface)',
        borderRight: '1px solid var(--color-border)', position: 'fixed', top: 0, left: 0, bottom: 0,
        display: 'flex', flexDirection: 'column', transition: 'width 0.2s', zIndex: 10,
      }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--color-border)' }}>
          {sidebarCollapsed ? (
            <span style={{ fontSize: 20 }}>🏥</span>
          ) : (
            <h2 style={{ fontSize: 16, fontWeight: 700 }}>医生工作台</h2>
          )}
        </div>
        <nav style={{ flex: 1, padding: '8px 12px' }}>
          {MENU.map((item) => {
            if (item.key === '-') {
              return <div key={item.label} style={{ height: 1, background: 'var(--color-border)', margin: '8px 0' }} />;
            }
            const active = loc.pathname === item.key || (item.key !== '/' && loc.pathname.startsWith(item.key));
            return (
              <div
                key={item.key}
                onClick={() => nav(item.key)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer', fontSize: 13, fontWeight: active ? 600 : 400,
                  color: active ? 'var(--color-primary)' : 'var(--color-text-secondary)',
                  background: active ? 'var(--color-primary-light)' : 'transparent',
                  marginBottom: 2, transition: '0.15s',
                }}
              >
                <span style={{ fontSize: 16 }}>{item.icon}</span>
                {!sidebarCollapsed && <span>{item.label}</span>}
              </div>
            );
          })}
        </nav>
        {!sidebarCollapsed && (
          <div style={{ padding: '12px 20px', borderTop: '1px solid var(--color-border)' }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', cursor: 'pointer' }}
                 onClick={() => { logout(); nav('/login'); }}>退出登录</div>
          </div>
        )}
      </aside>

      {/* Main */}
      <div style={{ marginLeft: sidebarCollapsed ? 56 : 'var(--sidebar-w)', flex: 1, transition: 'margin-left 0.2s' }}>
        <header style={{
          padding: '16px 24px', background: 'var(--color-surface)',
          borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', gap: 16,
        }}>
          <button onClick={toggleSidebar} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18 }}>☰</button>
          <span style={{ fontSize: 14, color: 'var(--color-text-secondary)' }}>
            {MENU.find(m => m.key === loc.pathname || (m.key !== '/' && loc.pathname.startsWith(m.key)))?.label || ''}
          </span>
        </header>
        <main style={{ padding: 24 }}>{children}</main>
      </div>
    </div>
  );
}