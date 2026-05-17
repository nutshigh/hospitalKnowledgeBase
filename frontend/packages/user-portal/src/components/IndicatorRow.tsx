import ColorBadge from './ColorBadge';

interface IndicatorRowProps {
  item_name: string;
  result_value?: string;
  unit?: string;
  ref_range_low?: string;
  ref_range_high?: string;
  color_level?: string;
  explanation?: string;
  expanded?: boolean;
  onToggle?: () => void;
}

export default function IndicatorRow({
  item_name, result_value, unit, ref_range_low, ref_range_high,
  color_level, explanation, expanded, onToggle,
}: IndicatorRowProps) {
  const refRange = ref_range_low && ref_range_high ? `${ref_range_low}-${ref_range_high}` : '';
  return (
    <div style={{ borderBottom: '1px solid var(--color-border-light)' }}>
      <div
        onClick={onToggle}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 0', cursor: 'pointer',
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>{item_name}</div>
          {refRange && <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 2 }}>参考: {refRange} {unit || ''}</div>}
        </div>
        <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 700 }}>{result_value || '-'}</span>
          {unit && <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>{unit}</span>}
          {color_level ? <ColorBadge level={color_level} size="sm" /> : null}
          <span style={{ fontSize: 14, color: 'var(--color-text-secondary)', transform: expanded ? 'rotate(90deg)' : 'none', transition: '0.2s' }}>›</span>
        </div>
      </div>
      {expanded && explanation && (
        <div style={{
          fontSize: 13, lineHeight: 1.7, color: 'var(--color-text-secondary)',
          background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)',
          padding: '12px', marginBottom: 12,
        }}>
          {explanation}
        </div>
      )}
    </div>
  );
}
