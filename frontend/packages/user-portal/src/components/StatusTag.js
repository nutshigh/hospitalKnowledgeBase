import { jsx as _jsx } from "react/jsx-runtime";
const STATUS_MAP = {
    queued: { label: '等待分析', color: '#A8A29E', bg: '#F5F5F4' },
    parsing: { label: '正在分析', color: '#0D9488', bg: '#CCFBF1' },
    processing: { label: 'AI 解读中', color: '#0D9488', bg: '#CCFBF1' },
    pending: { label: 'AI 解读中', color: '#0D9488', bg: '#CCFBF1' },
    completed: { label: '分析完成', color: '#16A34A', bg: '#DCFCE7' },
    failed: { label: '分析失败', color: '#DC2626', bg: '#FEE2E2' },
};
export default function StatusTag({ status }) {
    const s = STATUS_MAP[status] || STATUS_MAP.queued;
    return (_jsx("span", { style: {
            padding: '2px 10px', borderRadius: 12, fontSize: 12, fontWeight: 500,
            background: s.bg, color: s.color,
        }, children: s.label }));
}
