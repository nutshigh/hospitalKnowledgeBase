import ColorBadge from './ColorBadge';

interface IndicatorRowProps {
  item_name: string;
  result_value?: string;
  unit?: string;
  ref_range_low?: string;
  ref_range_high?: string;
  color_level?: string;
}

export default function IndicatorRow({
  item_name, result_value, unit, ref_range_low, ref_range_high, color_level,
}: IndicatorRowProps) {
  const refRange = ref_range_low && ref_range_high ? `${ref_range_low}-${ref_range_high}` : '';
  return (
    <div style={{ borderBottom: '1px solid var(--color-border-light)' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '14px 0',
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>{item_name}</div>
          {refRange && <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 2 }}>参考: {refRange} {unit || ''}</div>}
        </div>
        <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 700 }}>{result_value || '-'}</span>
          {unit && <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>{unit}</span>}
          {color_level ? <ColorBadge level={color_level} size="sm" /> : null}
        </div>
      </div>
    </div>
  );
}