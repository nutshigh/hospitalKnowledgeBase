import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import ColorBadge from './ColorBadge';
export default function IndicatorRow({ item_name, result_value, unit, ref_range_low, ref_range_high, color_level, }) {
    const refRange = ref_range_low && ref_range_high ? `${ref_range_low}-${ref_range_high}` : '';
    return (_jsx("div", { style: { borderBottom: '1px solid var(--color-border-light)' }, children: _jsxs("div", { style: {
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '14px 0',
            }, children: [_jsxs("div", { style: { flex: 1 }, children: [_jsx("div", { style: { fontSize: 14, fontWeight: 500 }, children: item_name }), refRange && _jsxs("div", { style: { fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 2 }, children: ["\u53C2\u8003: ", refRange, " ", unit || ''] })] }), _jsxs("div", { style: { textAlign: 'right', display: 'flex', alignItems: 'center', gap: 10 }, children: [_jsx("span", { style: { fontSize: 16, fontWeight: 700 }, children: result_value || '-' }), unit && _jsx("span", { style: { fontSize: 12, color: 'var(--color-text-secondary)' }, children: unit }), color_level ? _jsx(ColorBadge, { level: color_level, size: "sm" }) : null] })] }) }));
}
