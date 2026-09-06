import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
const COLOR_HEX = {
    red: '#ef4444',
    yellow: '#f59e0b',
    green: '#10b981',
};
export default function IndicatorTrendChart({ data }) {
    if (!data || data.length < 2)
        return null;
    const W = 220;
    const H = 50;
    const PAD_X = 8;
    const PAD_Y = 8;
    const values = data.map(d => d.value);
    const minV = Math.min(...values);
    const maxV = Math.max(...values);
    const span = maxV - minV || 1;
    const xStep = (W - 2 * PAD_X) / (data.length - 1);
    const yOf = (v) => H - PAD_Y - ((v - minV) / span) * (H - 2 * PAD_Y);
    const points = data.map((d, i) => `${PAD_X + i * xStep},${yOf(d.value)}`).join(' ');
    return (_jsxs("svg", { width: W, height: H, style: { display: 'block', marginTop: 4 }, children: [_jsx("polyline", { points: points, fill: "none", stroke: "var(--color-primary, #0D9488)", strokeWidth: "1.5", strokeLinejoin: "round", strokeLinecap: "round" }), data.map((d, i) => {
                const cx = PAD_X + i * xStep;
                const cy = yOf(d.value);
                const fill = d.color ? (COLOR_HEX[d.color] || '#0D9488') : '#0D9488';
                const dateLabel = d.report_date ? d.report_date.slice(5) : '';
                return (_jsxs("g", { children: [_jsx("circle", { cx: cx, cy: cy, r: "3", fill: fill, stroke: "#fff", strokeWidth: "1" }), _jsx("text", { x: cx, y: H - 1, fontSize: "8", fill: "var(--color-text-secondary, #888)", textAnchor: "middle", children: dateLabel })] }, d.report_id ?? i));
            })] }));
}
