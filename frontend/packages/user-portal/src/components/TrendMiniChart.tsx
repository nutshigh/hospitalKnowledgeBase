interface TrendPoint { year: number; result_value?: string; abnormal_rate: number }

export default function TrendMiniChart({ data }: { data: TrendPoint[] }) {
  if (!data || data.length < 2) return null;
  const max = Math.max(...data.map(d => d.abnormal_rate), 1);
  const h = 40;
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: h, padding: '8px 0' }}>
      {data.map((d, i) => (
        <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
          <div style={{
            width: '100%', height: `${(d.abnormal_rate / max) * h}px`,
            background: d.abnormal_rate > 50 ? 'var(--color-red)' : 'var(--color-primary)',
            borderRadius: '4px 4px 0 0', opacity: 0.7, transition: 'height 0.3s',
            minHeight: 2,
          }} />
          <span style={{ fontSize: 9, color: 'var(--color-text-secondary)' }}>{d.year}</span>
        </div>
      ))}
    </div>
  );
}
