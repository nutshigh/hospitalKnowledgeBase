import ColorBadge from './ColorBadge';

interface IndicatorRowProps {
  item_name: string;
  result_value?: string;
  unit?: string;
  ref_range_low?: string;
  ref_range_high?: string;
  color_level?: string;
  is_conclusion?: boolean;
}

export default function IndicatorRow({
  item_name, result_value, unit, ref_range_low, ref_range_high, color_level, is_conclusion,
}: IndicatorRowProps) {
  // 2026-08-31: 单限参考范围也显示(总胆固醇仅上限 → "<5.2"; 仅下限 → ">1.04")
  const valid = (v?: string) => v !== undefined && v !== null && v !== '' && v !== '无';
  const refRange = valid(ref_range_low) && valid(ref_range_high)
    ? `${ref_range_low}-${ref_range_high}`
    : valid(ref_range_high) ? `<${ref_range_high}`
    : valid(ref_range_low) ? `>${ref_range_low}`
    : '';
  const displayValue = is_conclusion ? '存在建议' : (result_value || '-');
  return (
    <div style={{ borderBottom: '1px solid var(--color-border-light)' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '14px 0',
      }}>
        <div style={{ flex: 1 }}>
          <div style={{
            fontSize: 14, fontWeight: 500,
            ...(is_conclusion ? { color: 'var(--color-text-secondary)' } : {}),
          }}>
            {is_conclusion ? '📋 ' : ''}{item_name}
          </div>
          {refRange && <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 2 }}>参考: {refRange} {unit || ''}</div>}
        </div>
        <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 700, color: is_conclusion ? 'var(--color-text-secondary)' : undefined }}>
            {displayValue}
          </span>
          {unit && <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>{unit}</span>}
          {color_level ? <ColorBadge level={color_level} size="sm" /> : null}
        </div>
      </div>
    </div>
  );
}
