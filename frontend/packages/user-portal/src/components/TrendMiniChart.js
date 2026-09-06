import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export default function TrendMiniChart({ data }) {
    if (!data || data.length < 2)
        return null;
    const max = Math.max(...data.map(d => d.abnormal_rate), 1);
    const h = 40;
    return (_jsx("div", { style: { display: 'flex', alignItems: 'flex-end', gap: 4, height: h, padding: '8px 0' }, children: data.map((d, i) => (_jsxs("div", { style: { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }, children: [_jsx("div", { style: {
                        width: '100%', height: `${(d.abnormal_rate / max) * h}px`,
                        background: d.abnormal_rate > 50 ? 'var(--color-red)' : 'var(--color-primary)',
                        borderRadius: '4px 4px 0 0', opacity: 0.7, transition: 'height 0.3s',
                        minHeight: 2,
                    } }), _jsx("span", { style: { fontSize: 9, color: 'var(--color-text-secondary)' }, children: d.year })] }, i))) }));
}
