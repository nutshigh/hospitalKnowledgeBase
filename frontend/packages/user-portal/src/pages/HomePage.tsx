import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Spin } from 'antd';
import { PlusOutlined, LogoutOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ReportCard from '../components/ReportCard';

export default function HomePage() {
  const { api, logout } = useUserStore();
  const nav = useNavigate();
  const [reports, setReports] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/reports').then(r => setReports(r.data.items || [])).catch(() => {}).finally(() => setLoading(false));
  }, []);

  return (
    <Layout title="我的报告">
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 20 }}>
        <Button type="text" icon={<LogoutOutlined />} onClick={() => { logout(); nav('/login'); }}
          style={{ color: 'var(--color-text-secondary)' }}>退出</Button>
      </div>
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : reports.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--color-text-secondary)' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>📋</div>
          <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 8 }}>暂无报告</div>
          <div style={{ fontSize: 13 }}>点击右下角上传您的第一份体检报告</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {reports.map((r: any) => (
            <ReportCard key={r.id} id={r.id} name={r.name || '体检报告'} report_date={r.report_date || ''} />
          ))}
        </div>
      )}
      <button
        onClick={() => nav('/upload')}
        style={{
          position: 'fixed', right: 24, bottom: 80, zIndex: 101,
          width: 48, height: 48, borderRadius: '50%',
          border: 'none', background: 'var(--color-primary)',
          color: '#fff', fontSize: 24, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
        }}
      >
        <PlusOutlined />
      </button>
    </Layout>
  );
}

function Button({ type, icon, onClick, children, style }: any) {
  return (
    <button onClick={onClick} style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, border: 'none', background: 'none',
      cursor: 'pointer', fontSize: 13, padding: '4px 8px', borderRadius: 6, ...style,
    }}>
      {icon} {children}
    </button>
  );
}
