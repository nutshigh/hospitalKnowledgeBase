import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Spin } from 'antd';
import { PlusOutlined, LogoutOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ReportCard from '../components/ReportCard';

export default function HomePage() {
  const { api, logout } = useUserStore();
  const nav = useNavigate();
  const loc = useLocation();
  const [reports, setReports] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const fetchOnce = () => api.get('/reports').then(r => {
      if (cancelled) return;
      setReports(r.data.items || []);
      // 只要还有未完成的报告（含 interp），每 10 秒轮询
      const stillRunning = (r.data.items || []).some((it: any) => {
        const ts = it.task_status, is = it.interp_status;
        if (ts && ts !== 'completed' && ts !== 'failed') return true;
        if (is && is !== 'completed' && is !== 'failed') return true;
        if (ts === 'completed' && !is) return true;
        return false;
      });
      if (stillRunning) timer = setTimeout(fetchOnce, 10000);
    }).catch(() => {});

    setLoading(true);
    fetchOnce().finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [loc.key]);

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
          {reports.map((r: any) => {
            const d = r.created_at ? new Date(r.created_at) : null;
            const dateStr = d ? `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}` : '';
            return (
              <ReportCard
                key={r.id}
                id={r.id}
                name={r.name || `体检报告 ${dateStr}`}
                report_date={dateStr}
                task_status={r.task_status}
                interp_status={r.interp_status}
                overall_level={r.overall_level}
              />
            );
          })}
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
