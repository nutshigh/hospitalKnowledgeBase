const STATUS_MAP: Record<string, { label: string; color: string; bg: string }> = {
  queued:    { label: '排队中', color: '#A8A29E', bg: '#F5F5F4' },
  parsing:   { label: '解析中', color: '#0D9488', bg: '#CCFBF1' },
  completed: { label: '已完成', color: '#16A34A', bg: '#DCFCE7' },
  failed:    { label: '失败',   color: '#DC2626', bg: '#FEE2E2' },
};

export default function StatusTag({ status }: { status: string }) {
  const s = STATUS_MAP[status] || STATUS_MAP.queued;
  return (
    <span style={{
      padding: '2px 10px', borderRadius: 12, fontSize: 12, fontWeight: 500,
      background: s.bg, color: s.color,
    }}>
      {s.label}
    </span>
  );
}
