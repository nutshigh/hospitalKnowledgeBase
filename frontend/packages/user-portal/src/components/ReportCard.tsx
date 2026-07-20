import { useNavigate } from 'react-router-dom';
import ColorBadge from './ColorBadge';
import StatusTag from './StatusTag';

interface ReportCardProps {
  id: number;
  name: string;
  report_date: string;
  overall_level?: string;
  status?: string;
  task_status?: string;
  interp_status?: string;
}

function effectiveStatus(task_status?: string, interp_status?: string): string {
  if (task_status === 'failed' || interp_status === 'failed') return 'failed';
  if (task_status && task_status !== 'completed') return task_status;  // queued / parsing
  if (!interp_status) return 'processing';  // task 完了, interp 还没起
  if (interp_status === 'completed') return 'completed';
  return 'processing';  // processing / pending
}

export default function ReportCard({ id, name, report_date, overall_level, status, task_status, interp_status }: ReportCardProps) {
  const nav = useNavigate();
  const displayStatus = status || effectiveStatus(task_status, interp_status);
  const showLevel = overall_level && displayStatus === 'completed';
  return (
    <div
      onClick={() => nav(`/report/${id}`)}
      style={{
        background: 'var(--color-surface)',
        borderRadius: 'var(--radius-md)',
        padding: '16px 20px',
        boxShadow: 'var(--shadow-sm)',
        border: '1px solid var(--color-border-light)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        cursor: 'pointer', transition: 'box-shadow 0.2s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.boxShadow = 'var(--shadow-md)')}
      onMouseLeave={(e) => (e.currentTarget.style.boxShadow = 'var(--shadow-sm)')}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {name || '体检报告'}
        </div>
        <div style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>{report_date}</div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        {showLevel ? <ColorBadge level={overall_level!} /> : null}
        {displayStatus ? <StatusTag status={displayStatus} /> : null}
        <span style={{ color: 'var(--color-text-secondary)', fontSize: 18 }}>›</span>
      </div>
    </div>
  );
}
