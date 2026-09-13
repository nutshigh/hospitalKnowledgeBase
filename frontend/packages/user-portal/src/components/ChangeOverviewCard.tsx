import { useEffect, useState } from 'react';
import { Spin } from 'antd';
import { useUserStore } from '../stores/userStore';
import ColorBadge from './ColorBadge';

interface ReportHead {
  report_id: number; report_date: string | null; overall_level: string | null;
  red_count: number; yellow_count: number; green_count: number;
}
interface IndicatorPoint {
  report_id: number; report_date: string | null; value: string; color: string | null;
}
interface KeyIndicator {
  item_name: string; unit: string | null; latest_value: string; latest_color: string | null;
  direction: string | null; delta_pct: number | null; points: IndicatorPoint[];
}
interface ChangeSummary {
  trend_summary: string; conclusion: string; suggestions: string; precautions: string;
}
interface ChangeOverview {
  reports: ReportHead[]; covered: number; reason?: string;
  key_indicators: KeyIndicator[]; summary: ChangeSummary | null; cached: boolean;
}

const CARD = {
  background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
  padding: '16px 20px', boxShadow: 'var(--shadow-sm)',
  border: '1px solid var(--color-border-light)', marginBottom: 16,
};

function AiBlock({ label, text, accent }: { label: string; text: string; accent?: boolean }) {
  return (
    <div style={{
      marginBottom: 10, padding: '8px 12px', borderRadius: 'var(--radius-sm)',
      background: accent ? 'var(--color-primary-light)' : 'var(--color-bg)',
      borderLeft: `3px solid ${accent ? 'var(--color-red)' : 'var(--color-primary)'}`,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--color-text)', whiteSpace: 'pre-wrap' }}>{text}</div>
    </div>
  );
}

export default function ChangeOverviewCard() {
  const { api } = useUserStore();
  const [data, setData] = useState<ChangeOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    api.get('/profile/change-overview')
      .then(r => { setData(r.data); setFailed(false); })
      .catch(() => { setData(null); setFailed(true); })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div style={CARD}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>📈 近期健康变化</div>
        <div style={{ textAlign: 'center', padding: 16 }}><Spin size="small" /> 正在生成跨报告分析...</div>
      </div>
    );
  }
  if (failed) {
    return (
      <div style={CARD}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>📈 近期健康变化</div>
        <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>
          健康变化总览暂时无法获取,请稍后刷新重试。
        </div>
      </div>
    );
  }
  if (!data || data.reason === 'insufficient' || data.covered < 2) {
    return (
      <div style={CARD}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>📈 近期健康变化</div>
        <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>
          完成 ≥2 份报告的 AI 解读后,这里将自动对比最近 {data?.covered ?? 0} 份报告并生成健康变化总览。
        </div>
      </div>
    );
  }

  const firstDate = data.reports[0]?.report_date;
  const lastDate = data.reports[data.reports.length - 1]?.report_date;
  const range = [firstDate, lastDate].filter(Boolean).join(' ~ ');
  const shown = showAll ? data.key_indicators : data.key_indicators.slice(0, 5);
  const s = data.summary;

  return (
    <div style={CARD}>
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>📈 近期健康变化</div>
      <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 12 }}>
        已自动对比最近 {data.covered} 份已完成解读的报告{range ? `(${range})` : ''},无需手动选择
      </div>

      {s ? (
        <>
          <AiBlock label="总体变化" text={s.trend_summary} accent />
          <AiBlock label="结论" text={s.conclusion} />
          <AiBlock label="建议" text={s.suggestions} />
          <AiBlock label="注意事项" text={s.precautions} />
        </>
      ) : (
        <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', padding: '4px 0 12px' }}>
          AI 总览暂不可用,请查看下方关键指标变化。
        </div>
      )}

      {data.key_indicators.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 4 }}>
            关键指标变化
          </div>
          {shown.map((k, i) => {
            const dir = k.direction === 'down' ? '↓' : (k.direction === 'up' ? '↑' : '');
            const pct = k.delta_pct != null ? `${Math.abs(Math.round(k.delta_pct * 10) / 10)}%` : '';
            return (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 0', borderBottom: '1px solid var(--color-border-light)', fontSize: 13,
              }}>
                <span style={{ flex: 1, fontWeight: 500, minWidth: 0 }}>{k.item_name}</span>
                <span style={{ color: 'var(--color-text-secondary)', whiteSpace: 'nowrap' }}>
                  {k.latest_value}{k.unit ? ` ${k.unit}` : ''}
                  {k.latest_color && <ColorBadge level={k.latest_color} size="sm" />}
                </span>
                <span style={{
                  whiteSpace: 'nowrap', fontSize: 12, fontWeight: 600,
                  color: dir === '↑' ? 'var(--color-red)' : 'var(--color-green)',
                }}>
                  {dir}{pct}
                </span>
              </div>
            );
          })}
          {data.key_indicators.length > 5 && (
            <button onClick={() => setShowAll(!showAll)} style={{
              border: 'none', background: 'none', color: 'var(--color-primary)',
              fontSize: 12, cursor: 'pointer', padding: '8px 0',
            }}>
              {showAll ? '收起' : `展开全部 (${data.key_indicators.length})`}
            </button>
          )}
        </div>
      )}

      <div style={{ marginTop: 12, fontSize: 11, color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>
        本内容由 AI 依据指标数值自动生成,仅供参考,不构成医疗诊断;指标异常请遵医嘱复查。
      </div>
    </div>
  );
}
