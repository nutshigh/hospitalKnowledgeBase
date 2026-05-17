import { CSSProperties } from 'react';

const COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  red:    { bg: 'var(--color-red-light)',  text: 'var(--color-red)',    dot: 'var(--color-red)' },
  yellow: { bg: 'var(--color-yellow-light)', text: 'var(--color-yellow)', dot: 'var(--color-yellow)' },
  green:  { bg: 'var(--color-green-light)', text: 'var(--color-green)', dot: 'var(--color-green)' },
};

export default function ColorBadge({ level, size = 'md' }: { level: string; size?: 'sm' | 'md' }) {
  const c = COLORS[level] || COLORS.green;
  const s = size === 'sm' ? { gap: 4, px: 8, py: 2, fz: 12, dot: 6 } : { gap: 6, px: 12, py: 4, fz: 13, dot: 8 };
  const style: CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: s.gap,
    padding: `${s.py}px ${s.px}px`, borderRadius: 20,
    background: c.bg, color: c.text, fontSize: s.fz, fontWeight: 600,
  };
  return (
    <span style={style}>
      <span style={{ width: s.dot, height: s.dot, borderRadius: '50%', background: c.dot }} />
      {{ red: '红区', yellow: '黄区', green: '绿区' }[level] || level}
    </span>
  );
}
