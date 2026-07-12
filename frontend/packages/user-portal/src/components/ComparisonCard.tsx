import { useEffect, useState } from 'react';
import { Select, Spin, message } from 'antd';
import { useUserStore } from '../stores/userStore';

interface CompareData {
  current: { report_id: number; report_date: string; overall_level: string;
    red_count: number; yellow_count: number; green_count: number; };
  baseline: { report_id: number; report_date: string; overall_level: string;
    red_count: number; yellow_count: number; green_count: number; } | null;
  delta_summary: { red_delta: number; yellow_delta: number; green_delta: number };
  indicators: Array<{
    item_name: string; current_value: string; baseline_value: string;
    unit: string; current_color: string; baseline_color: string;
    delta: number | null; delta_pct: number | null; status: string | null;
  }>;
  only_in_current: Array<{ item_name: string }>;
  only_in_baseline: Array<{ item_name: string }>;
  ai_summary: string;
  ai_summary_cached: boolean;
}

interface HistoryItem { id: number; report_date?: string; name?: string; created_at?: string; }

function DeltaBadge({ delta }: { delta: number }) {
  if (delta === 0) return <span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>-</span>;
  const isUp = delta > 0;
  return (
    <span style={{
      fontSize: 12, fontWeight: 600,
      color: isUp ? 'var(--color-red)' : 'var(--color-green)',
    }}>
      {isUp ? '↑' : '↓'}{Math.abs(delta)}
    </span>
  );
}

function StatusTag({ status }: { status: string | null }) {
  if (!status) return null;
  const map: Record<string, string> = {
    improved: 'var(--color-green)',
    worsened: 'var(--color-red)',
    stable: 'var(--color-text-secondary)',
  };
  const labelMap: Record<string, string> = { improved: '改善', worsened: '恶化', stable: '持平' };
  return <span style={{ fontSize: 11, color: map[status] || '#888' }}>{labelMap[status]}</span>;
}

export default function ComparisonCard({ reportId, baselineId: initialBaseline }: {
  reportId: number; baselineId?: number;
}) {
  const { api } = useUserStore();
  const [data, setData] = useState<CompareData | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [currentBaseline, setCurrentBaseline] = useState<number | undefined>(initialBaseline);
  const [loading, setLoading] = useState(true);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [summaryExpanded, setSummaryExpanded] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.get('/profile/compare', { params: { report_id: reportId, baseline_id: currentBaseline } })
      .then(r => {
        setData(r.data);
        if (r.data?.baseline?.report_id && currentBaseline === undefined) {
          setCurrentBaseline(r.data.baseline.report_id);
        }
      })
      .catch(() => { setData(null); })
      .finally(() => setLoading(false));
    api.get('/reports').then(r => setHistory(r.data.items || [])).catch(() => {});
  }, [reportId]);

  const switchBaseline = async (id: number) => {
    setCurrentBaseline(id);
    if (!data) return;
    setSummaryLoading(true);
    try {
      const r = await api.get('/profile/ai-summary', { params: { report_id: reportId, baseline_id: id } });
      setData({ ...data, ai_summary: r.data.ai_summary || '', ai_summary_cached: r.data.cached });
    } catch {
      message.error('AI 小结切换失败');
    } finally {
      setSummaryLoading(false);
    }
  };

  if (loading) return <div style={{ textAlign: 'center', padding: 16 }}><Spin /></div>;
  if (!data || !data.baseline) return null;

  const histOptions = history
    .filter(h => h.id !== reportId && (!data.baseline || h.id !== data.baseline.report_id))
    .map(h => ({
      value: h.id,
      label: h.report_date ? `${h.report_date}${h.name ? ' · ' + h.name : ''}` : `报告 ${h.id}`,
    }));
  const baseOpt = data.baseline ? [{
    value: data.baseline.report_id,
    label: data.baseline.report_date
      ? `${data.baseline.report_date}${data.baseline.overall_level ? ' · ' + data.baseline.overall_level : ''}`
      : `报告 ${data.baseline.report_id}`,
  }] : [];
  const allOptions = [...baseOpt, ...histOptions];

  const indicatorsToShow = expanded ? data.indicators : data.indicators.slice(0, 6);

  return (
    <div style={{
      background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
      padding: 16, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
      marginBottom: 20,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>📊 与上次报告对比</span>
        <Select
          size="small" value={currentBaseline} style={{ width: 180 }}
          onChange={switchBaseline} options={allOptions} loading={summaryLoading}
        />
      </div>

      <div style={{
        display: 'flex', gap: 12, padding: '8px 12px', background: 'var(--color-bg)',
        borderRadius: 'var(--radius-sm)', marginBottom: 12, fontSize: 12,
      }}>
        <span>红区 <b style={{ color: 'var(--color-red)' }}>{data.baseline.red_count}</b> →
          <b style={{ color: 'var(--color-red)' }}>{data.current.red_count}</b>
          <DeltaBadge delta={data.delta_summary.red_delta} />
        </span>
        <span>黄区 <b style={{ color: 'var(--color-yellow)' }}>{data.baseline.yellow_count}</b> →
          <b style={{ color: 'var(--color-yellow)' }}>{data.current.yellow_count}</b>
          <DeltaBadge delta={data.delta_summary.yellow_delta} />
        </span>
        <span>绿区 <b style={{ color: 'var(--color-green)' }}>{data.baseline.green_count}</b> →
          <b style={{ color: 'var(--color-green)' }}>{data.current.green_count}</b>
          <DeltaBadge delta={data.delta_summary.green_delta} />
        </span>
      </div>

      {data.indicators.length > 0 && (
        <div>
          <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 6 }}>
            指标差异
          </div>
          {indicatorsToShow.map((ind, i) => (
            <div key={i} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '8px 0', borderBottom: '1px solid var(--color-border-light)',
              fontSize: 12,
            }}>
              <span style={{ flex: 1, fontWeight: 500 }}>{ind.item_name}</span>
              <span style={{ color: 'var(--color-text-secondary)' }}>
                {ind.baseline_value} → <b style={{ color: 'var(--color-text)' }}>{ind.current_value}</b>
                {ind.unit ? ` ${ind.unit}` : ''}
              </span>
              <span style={{ marginLeft: 12, minWidth: 56, textAlign: 'right' }}>
                {ind.delta !== null ? (
                  <>
                    <span style={{
                      color: ind.delta > 0 ? 'var(--color-red)' : 'var(--color-green)',
                      fontWeight: 600,
                    }}>
                      {ind.delta > 0 ? '+' : ''}{ind.delta}
                    </span>{' '}
                    <StatusTag status={ind.status} />
                  </>
                ) : null}
              </span>
            </div>
          ))}
          {data.indicators.length > 6 && (
            <button
              onClick={() => setExpanded(!expanded)}
              style={{
                border: 'none', background: 'none', color: 'var(--color-primary)',
                fontSize: 12, cursor: 'pointer', padding: '8px 0',
              }}
            >
              {expanded ? '收起' : `展开全部 (${data.indicators.length})`}
            </button>
          )}
        </div>
      )}

      {(data.ai_summary || summaryLoading) && (
        <div style={{
          marginTop: 12, padding: '10px 12px', background: 'var(--color-bg)',
          borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--color-primary)',
        }}>
          <div
            onClick={() => setSummaryExpanded(!summaryExpanded)}
            style={{ fontSize: 12, fontWeight: 600, cursor: 'pointer', color: 'var(--color-primary)', marginBottom: 4 }}
          >
            AI 健康变化小结 {summaryLoading ? <Spin size="small" /> : (data.ai_summary_cached ? '(已缓存)' : '(新生成)')} {summaryExpanded ? '▾' : '▸'}
          </div>
          {summaryExpanded && (
            <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--color-text)' }}>
              {summaryLoading ? '生成中...' : (data.ai_summary || 'AI 小结暂不可用,查看上方指标对比详情')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
