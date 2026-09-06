import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Spin, Input } from 'antd';
import { UserOutlined, LogoutOutlined, SettingOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import IndicatorTrendChart from '../components/IndicatorTrendChart';
export default function ProfilePage() {
    const { api, logout } = useUserStore();
    const nav = useNavigate();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    useEffect(() => {
        api.get('/profile/overview').then(r => setData(r.data)).catch(() => setData(null)).finally(() => setLoading(false));
    }, []);
    if (loading)
        return (_jsx(Layout, { title: "\u6211\u7684\u5065\u5EB7\u6863\u6848", children: _jsx("div", { style: { textAlign: 'center', padding: 60 }, children: _jsx(Spin, { size: "large" }) }) }));
    if (!data || !data.user_summary || data.user_summary.total_reports === 0) {
        return (_jsxs(Layout, { title: "\u6211\u7684\u5065\u5EB7\u6863\u6848", children: [_jsxs("div", { style: { textAlign: 'center', padding: 60, color: 'var(--color-text-secondary)' }, children: [_jsx("div", { style: { fontSize: 48, marginBottom: 16 }, children: "\uD83D\uDCCB" }), _jsx("div", { style: { fontSize: 15, fontWeight: 500, marginBottom: 8 }, children: "\u6682\u65E0\u6863\u6848\u6570\u636E" }), _jsx("div", { style: { fontSize: 13 }, children: "\u4E0A\u4F20\u60A8\u7684\u4F53\u68C0\u62A5\u544A\u540E,\u53EF\u5728\u6B64\u67E5\u770B\u5065\u5EB7\u53D8\u5316\u8D8B\u52BF" })] }), _jsx(BottomSettings, { onLogout: () => { logout(); nav('/login'); } })] }));
    }
    const s = data.user_summary;
    const filtered = data.indicator_trends.filter(t => {
        if (!search)
            return true;
        return (t.item_name_standard || t.item_name || '').toLowerCase().includes(search.toLowerCase());
    });
    const topTrends = filtered.slice(0, 10);
    return (_jsxs(Layout, { title: "\u6211\u7684\u5065\u5EB7\u6863\u6848", children: [_jsxs("div", { style: {
                    background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
                    padding: 20, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 16,
                }, children: [_jsxs("div", { style: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }, children: [_jsx("div", { style: { width: 48, height: 48, borderRadius: '50%', background: 'var(--color-primary-light)',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center' }, children: _jsx(UserOutlined, { style: { fontSize: 20, color: 'var(--color-primary)' } }) }), _jsxs("div", { children: [_jsx("div", { style: { fontWeight: 600, fontSize: 15 }, children: "\u4F53\u68C0\u7528\u6237" }), _jsxs("div", { style: { fontSize: 12, color: 'var(--color-text-secondary)' }, children: ["\u5171 ", s.total_reports, " \u4EFD\u62A5\u544A \u00B7 ", s.earliest_date || '未知', " \u81F3 ", s.latest_date || '未知'] })] }), s.latest_overall_level && (_jsx("div", { style: { marginLeft: 'auto' }, children: _jsx(ColorBadge, { level: s.latest_overall_level }) }))] }), _jsxs("div", { style: { display: 'flex', gap: 8, fontSize: 12 }, children: [_jsxs("span", { style: { color: 'var(--color-red)', fontWeight: 600 }, children: ["\u7EA2\u533A ", s.latest_red] }), _jsx("span", { style: { color: 'var(--color-border)' }, children: "|" }), _jsxs("span", { style: { color: 'var(--color-yellow)', fontWeight: 600 }, children: ["\u9EC4\u533A ", s.latest_yellow] }), _jsx("span", { style: { color: 'var(--color-border)' }, children: "|" }), _jsxs("span", { style: { color: 'var(--color-green)', fontWeight: 600 }, children: ["\u7EFF\u533A ", s.latest_green] })] })] }), data.abnormal_distribution.length > 0 && (_jsxs("div", { style: {
                    background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
                    padding: '16px 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 16,
                }, children: [_jsx("div", { style: { fontWeight: 600, fontSize: 14, marginBottom: 8 }, children: "\u5F02\u5E38\u6307\u6807\u5206\u5E03" }), data.abnormal_distribution.map((a, i) => (_jsxs("div", { style: {
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                            padding: '8px 0', borderBottom: i < data.abnormal_distribution.length - 1 ? '1px solid var(--color-border-light)' : 'none',
                            fontSize: 13,
                        }, children: [_jsx("span", { children: a.item_name_standard }), _jsxs("span", { style: { color: 'var(--color-text-secondary)', fontSize: 12 }, children: ["\u7EA2 ", a.red_count, " \u00B7 \u9EC4 ", a.yellow_count, ' ', _jsx(ColorBadge, { level: a.last_color, size: "sm" })] })] }, i)))] })), _jsxs("div", { style: {
                    background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
                    padding: '16px 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
                }, children: [_jsx("div", { style: { fontWeight: 600, fontSize: 14, marginBottom: 8 }, children: "\u6307\u6807\u8D70\u52BF" }), _jsx(Input.Search, { placeholder: "\u641C\u7D22\u6307\u6807\u540D", size: "small", value: search, onChange: e => setSearch(e.target.value), style: { marginBottom: 12 } }), topTrends.length === 0 ? (_jsx("div", { style: { textAlign: 'center', padding: 24, color: 'var(--color-text-secondary)', fontSize: 13 }, children: "\u6682\u65E0\u53EF\u89C6\u5316\u6307\u6807" })) : (topTrends.map((t, i) => {
                        const last = t.points[t.points.length - 1];
                        return (_jsxs("div", { style: {
                                padding: '10px 0', borderBottom: i !== topTrends.length - 1 ? '1px solid var(--color-border-light)' : 'none',
                            }, children: [_jsxs("div", { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }, children: [_jsxs("span", { style: { fontSize: 13, fontWeight: 500 }, children: [t.item_name_standard || t.item_name, t.trend_direction && (_jsx("span", { style: {
                                                        marginLeft: 6, fontSize: 11,
                                                        color: t.trend_direction === 'up' ? 'var(--color-red)' : 'var(--color-green)',
                                                    }, children: t.trend_direction === 'up' ? '↑' : '↓' }))] }), _jsxs("span", { style: { fontSize: 12, color: 'var(--color-text-secondary)' }, children: [last ? `${last.value}${t.unit ? ' ' + t.unit : ''}` : '-', last?.color && _jsx(ColorBadge, { level: last.color, size: "sm" })] })] }), _jsx(IndicatorTrendChart, { data: t.points })] }, i));
                    }))] }), _jsx(BottomSettings, { onLogout: () => { logout(); nav('/login'); } })] }));
}
function BottomSettings({ onLogout }) {
    return (_jsxs("div", { style: {
            marginTop: 24, background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
            overflow: 'hidden', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
        }, children: [_jsxs("div", { onClick: () => { }, style: {
                    display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
                    cursor: 'pointer', borderBottom: '1px solid var(--color-border-light)',
                }, children: [_jsx(SettingOutlined, {}), _jsx("span", { style: { fontSize: 14 }, children: "\u8BBE\u7F6E" }), _jsx("span", { style: { marginLeft: 'auto', color: 'var(--color-text-secondary)' }, children: "\u203A" })] }), _jsxs("div", { onClick: onLogout, style: {
                    display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
                    cursor: 'pointer', color: 'var(--color-red)',
                }, children: [_jsx(LogoutOutlined, {}), _jsx("span", { style: { fontSize: 14 }, children: "\u9000\u51FA\u767B\u5F55" })] })] }));
}
