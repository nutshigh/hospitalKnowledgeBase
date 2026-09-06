import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Select, Spin, message } from 'antd';
import { useUserStore } from '../stores/userStore';
function DeltaBadge({ delta }) {
    if (delta === 0)
        return _jsx("span", { style: { color: 'var(--color-text-secondary)', fontSize: 12 }, children: "-" });
    const isUp = delta > 0;
    return (_jsxs("span", { style: {
            fontSize: 12, fontWeight: 600,
            color: isUp ? 'var(--color-red)' : 'var(--color-green)',
        }, children: [isUp ? '↑' : '↓', Math.abs(delta)] }));
}
function StatusTag({ status }) {
    if (!status)
        return null;
    const map = {
        improved: 'var(--color-green)',
        worsened: 'var(--color-red)',
        stable: 'var(--color-text-secondary)',
    };
    const labelMap = { improved: '改善', worsened: '恶化', stable: '持平' };
    return _jsx("span", { style: { fontSize: 11, color: map[status] || '#888' }, children: labelMap[status] });
}
export default function ComparisonCard({ reportId, baselineId: initialBaseline }) {
    const { api } = useUserStore();
    const [data, setData] = useState(null);
    const [history, setHistory] = useState([]);
    const [currentBaseline, setCurrentBaseline] = useState(initialBaseline);
    const [loading, setLoading] = useState(true);
    const [summaryLoading, setSummaryLoading] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const [summaryExpanded, setSummaryExpanded] = useState(true);
    useEffect(() => {
        setLoading(true);
        api.get('/profile/compare', { params: { report_id: reportId, baseline_id: currentBaseline } })
            .then(r => {
            setData(r.data);
            if (r.data?.baseline?.report_id && currentBaseline === undefined) {
                setCurrentBaseline(r.data.baseline.report_id);
            }
        })
            .catch(() => { setData(null); })
            .finally(() => setLoading(false));
        api.get('/reports').then(r => setHistory(r.data.items || [])).catch(() => { });
    }, [reportId]);
    const switchBaseline = async (id) => {
        setCurrentBaseline(id);
        setSummaryLoading(true);
        try {
            const r = await api.get('/profile/ai-summary', { params: { report_id: reportId, baseline_id: id } });
            setData(prev => prev ? {
                ...prev,
                ai_summary: r.data.ai_summary || '',
                ai_summary_cached: r.data.cached,
            } : prev);
        }
        catch {
            message.error('AI 小结切换失败');
        }
        finally {
            setSummaryLoading(false);
        }
    };
    if (loading)
        return _jsx("div", { style: { textAlign: 'center', padding: 16 }, children: _jsx(Spin, {}) });
    if (!data || !data.baseline)
        return null;
    const others = history.filter(h => h.id !== reportId);
    const _time = (h) => (h.created_at || '').replace('T', ' ').slice(0, 16);
    const _key = (h) => `${h.name ?? ''}\u0001${_time(h)}`;
    const freq = new Map();
    others.forEach(h => freq.set(_key(h), (freq.get(_key(h)) ?? 0) + 1));
    // 标签 = 姓名 + 上传时间(created_at 恒有值,不像 report_date 会缺,避免"报告 3"这类认不出人的项);
    // 同姓名同时刻(批量同秒上传)才追加 #id 去歧义。
    const optLabel = (h) => {
        const t = _time(h);
        const dup = (freq.get(_key(h)) ?? 0) > 1 ? ` #${h.id}` : '';
        const head = h.name ? h.name : (h.report_date || `报告 ${h.id}`);
        return t ? `${head} · ${t}${dup}` : `${head}${dup}`;
    };
    const histOptions = others
        .filter(h => h.id !== data.baseline.report_id)
        .map(h => ({ value: h.id, label: optLabel(h) }));
    const baseHist = data.baseline ? others.find(h => h.id === data.baseline.report_id) : undefined;
    const baseOpt = data.baseline ? [{
            value: data.baseline.report_id,
            label: baseHist ? optLabel(baseHist) : `报告 ${data.baseline.report_id}`,
        }] : [];
    const allOptions = [...baseOpt, ...histOptions];
    const indicatorsToShow = expanded ? data.indicators : data.indicators.slice(0, 6);
    return (_jsxs("div", { style: {
            background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
            padding: 16, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
            marginBottom: 20,
        }, children: [_jsxs("div", { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }, children: [_jsx("span", { style: { fontWeight: 600, fontSize: 14 }, children: "\uD83D\uDCCA \u4E0E\u5386\u53F2\u62A5\u544A\u5BF9\u6BD4" }), _jsx(Select, { size: "small", value: currentBaseline, style: { width: 180 }, onChange: switchBaseline, options: allOptions, loading: summaryLoading })] }), _jsxs("div", { style: {
                    display: 'flex', gap: 12, padding: '8px 12px', background: 'var(--color-bg)',
                    borderRadius: 'var(--radius-sm)', marginBottom: 12, fontSize: 12,
                }, children: [_jsxs("span", { children: ["\u7EA2\u533A ", _jsx("b", { style: { color: 'var(--color-red)' }, children: data.baseline.red_count }), " \u2192", _jsx("b", { style: { color: 'var(--color-red)' }, children: data.current.red_count }), _jsx(DeltaBadge, { delta: data.delta_summary.red_delta })] }), _jsxs("span", { children: ["\u9EC4\u533A ", _jsx("b", { style: { color: 'var(--color-yellow)' }, children: data.baseline.yellow_count }), " \u2192", _jsx("b", { style: { color: 'var(--color-yellow)' }, children: data.current.yellow_count }), _jsx(DeltaBadge, { delta: data.delta_summary.yellow_delta })] }), _jsxs("span", { children: ["\u7EFF\u533A ", _jsx("b", { style: { color: 'var(--color-green)' }, children: data.baseline.green_count }), " \u2192", _jsx("b", { style: { color: 'var(--color-green)' }, children: data.current.green_count }), _jsx(DeltaBadge, { delta: data.delta_summary.green_delta })] })] }), data.indicators.length > 0 && (_jsxs("div", { children: [_jsx("div", { style: { fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 6 }, children: "\u6307\u6807\u5DEE\u5F02" }), indicatorsToShow.map((ind, i) => (_jsxs("div", { style: {
                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                            padding: '8px 0', borderBottom: '1px solid var(--color-border-light)',
                            fontSize: 12,
                        }, children: [_jsx("span", { style: { flex: 1, fontWeight: 500 }, children: ind.item_name }), _jsxs("span", { style: { color: 'var(--color-text-secondary)' }, children: [ind.baseline_value, " \u2192 ", _jsx("b", { style: { color: 'var(--color-text)' }, children: ind.current_value }), ind.unit ? ` ${ind.unit}` : ''] }), _jsx("span", { style: { marginLeft: 12, minWidth: 56, textAlign: 'right' }, children: ind.delta !== null ? (_jsxs(_Fragment, { children: [_jsxs("span", { style: {
                                                color: ind.delta > 0 ? 'var(--color-red)' : 'var(--color-green)',
                                                fontWeight: 600,
                                            }, children: [ind.delta > 0 ? '+' : '', ind.delta] }), ' ', _jsx(StatusTag, { status: ind.status })] })) : null })] }, i))), data.indicators.length > 6 && (_jsx("button", { onClick: () => setExpanded(!expanded), style: {
                            border: 'none', background: 'none', color: 'var(--color-primary)',
                            fontSize: 12, cursor: 'pointer', padding: '8px 0',
                        }, children: expanded ? '收起' : `展开全部 (${data.indicators.length})` }))] })), (data.ai_summary || summaryLoading) && (_jsxs("div", { style: {
                    marginTop: 12, padding: '10px 12px', background: 'var(--color-bg)',
                    borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--color-primary)',
                }, children: [_jsxs("div", { onClick: () => setSummaryExpanded(!summaryExpanded), style: { fontSize: 12, fontWeight: 600, cursor: 'pointer', color: 'var(--color-primary)', marginBottom: 4 }, children: ["AI \u5065\u5EB7\u53D8\u5316\u5C0F\u7ED3 ", summaryLoading ? _jsx(Spin, { size: "small" }) : (data.ai_summary_cached ? '(已缓存)' : '(新生成)'), " ", summaryExpanded ? '▾' : '▸'] }), summaryExpanded && (_jsx("div", { style: { fontSize: 13, lineHeight: 1.6, color: 'var(--color-text)' }, children: summaryLoading ? '生成中...' : (data.ai_summary || 'AI 小结暂不可用,查看上方指标对比详情') }))] }))] }));
}
