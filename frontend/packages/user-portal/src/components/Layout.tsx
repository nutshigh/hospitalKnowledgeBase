import { ReactNode } from 'react';

export default function Layout({ children, title }: { children: ReactNode; title?: string }) {
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
    </div>
  );
}
