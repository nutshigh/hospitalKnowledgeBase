import { useMemo } from 'react';
import { Drawer } from 'antd';
import bodyBg from '../assets/body_background.jpg';
import { layoutLabels, type ColorLevel } from './bodyHealthMap/organMap';

interface BodyHealthMapProps {
  open: boolean;
  onClose: () => void;
  indicators: any[];
}

const LEVEL_COLOR: Record<ColorLevel, string> = {
  red: 'var(--color-red)',
  yellow: 'var(--color-yellow)',
};
const LEVEL_BG: Record<ColorLevel, string> = {
  red: 'var(--color-red-light)',
  yellow: 'var(--color-yellow-light)',
};
const DOT = { display: 'inline-block', width: 8, height: 8, borderRadius: '50%', marginRight: 4 } as const;

export default function BodyHealthMap({ open, onClose, indicators }: BodyHealthMapProps) {
  const abnormal = useMemo(
    () => (indicators || [])
      .filter((i: any) => i.color_level === 'red' || i.color_level === 'yellow')
      .map((i: any) => ({
        name: i.explanation || i.item_name,
        level: i.color_level as ColorLevel,
        value: i.result_value,
        originLine: i.origin_line,
      })),
    [indicators],
  );
  const { labels, others } = useMemo(() => layoutLabels(abnormal), [abnormal]);

  return (
    <Drawer
      placement="bottom"
      open={open}
      onClose={onClose}
      height="88vh"
      title="人体健康图"
      styles={{ body: { padding: '12px 16px 24px' } }}
    >
      <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 8 }}>
        <span><span style={{ ...DOT, background: LEVEL_COLOR.red }} />红区</span>
        <span><span style={{ ...DOT, background: LEVEL_COLOR.yellow }} />黄区</span>
      </div>

      <div style={{ position: 'relative', width: '100%', maxWidth: 340, margin: '0 auto', aspectRatio: '536 / 825' }}>
        <img src={bodyBg} alt="人体示意图" style={{ width: '100%', display: 'block' }} />
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
        >
          {labels.map((l, i) => (
            <line
              key={i}
              x1={l.x} y1={l.y} x2={l.anchorX} y2={l.anchorY}
              stroke={LEVEL_COLOR[l.level]}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {labels.map((l, i) => (
          <div
            key={i}
            style={{
              position: 'absolute',
              top: `${l.y}%`,
              left: `${l.x}%`,
              transform: l.x < 50 ? 'translate(-100%, -50%)' : 'translate(0, -50%)',
              maxWidth: 120,
            }}
          >
            <span style={{
              display: 'inline-block',
              padding: '2px 8px',
              borderRadius: 12,
              fontSize: 12,
              fontWeight: 600,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              maxWidth: 120,
              color: LEVEL_COLOR[l.level],
              background: LEVEL_BG[l.level],
              border: `1px solid ${LEVEL_COLOR[l.level]}`,
            }}>
              {l.name}
            </span>
          </div>
        ))}
      </div>

      {labels.length === 0 && (
        <div style={{
          marginTop: 12, padding: '10px 12px', borderRadius: 8,
          background: 'var(--color-bg)', border: '1px solid var(--color-border-light)',
          fontSize: 13, lineHeight: 1.6, color: 'var(--color-text-secondary)',
        }}>
          人体图暂未显示可定位的异常
          {others.length > 0 ? '，详情请查看下方「全身性 / 其他异常」。' : '，详情请查看报告指标列表。'}
        </div>
      )}

      {others.length > 0 && (
        <div style={{ marginTop: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>全身性 / 其他异常</div>
          {others.map((o, i) => (
            <div
              key={i}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '10px 0', borderBottom: '1px solid var(--color-border-light)',
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: LEVEL_COLOR[o.level], flexShrink: 0 }} />
              <span style={{ flex: 1, fontSize: 14 }}>{o.name}</span>
              {o.value != null && o.value !== '' ? <span style={{ fontSize: 14, fontWeight: 600 }}>{o.value}</span> : null}
            </div>
          ))}
        </div>
      )}
    </Drawer>
  );
}
