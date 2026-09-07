import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Spin, Button, Popconfirm, message, Collapse } from 'antd';
import { ArrowLeftOutlined, DeleteOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import IndicatorRow from '../components/IndicatorRow';
import StatusTag from '../components/StatusTag';
import ChatPanel from '../components/ChatPanel';
import ComparisonCard from '../components/ComparisonCard';
import { useChatStore } from '../stores/chatStore';
import { InterpretationReportCard } from '@hospital/shared';
const COLOR_ORDER = { red: 0, yellow: 1, green: 2 };
function sortByColor(items) {
    return [...items].sort((a, b) => (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));
}
function toGroups(indicators, moduleOrder) {
    const hasOrder = Array.isArray(moduleOrder) && moduleOrder.length > 0;
    if (!hasOrder) {
        // 旧后端无 module_order → 退化为今天的整体平铺(红黄绿优先)
        return { groups: [], flat: sortByColor(indicators) };
    }
    const groups = new Map();
    const flat = [];
    for (const ind of indicators) {
        if (ind.group && moduleOrder.includes(ind.group)) {
            if (!groups.has(ind.group))
                groups.set(ind.group, []);
            groups.get(ind.group).push(ind);
        }
        else {
            flat.push(ind);
        }
    }
    return {
        groups: moduleOrder.filter((g) => groups.has(g))
            .map((name) => ({ name, items: sortByColor(groups.get(name)) })),
        flat: sortByColor(flat),
    };
}
function countLevels(items) {
    const c = { red: 0, yellow: 0, green: 0 };
    for (const it of items) {
        const l = it.color_level;
        if (l === 'red' || l === 'yellow' || l === 'green')
            c[l] += 1;
    }
    return c;
}
export default function ReportDetailPage() {
    const { id } = useParams();
    const { api } = useUserStore();
    const nav = useNavigate();
    const [report, setReport] = useState(null);
    const [interpretation, setInterpretation] = useState(null);
    const [loading, setLoading] = useState(true);
    const chatStore = useChatStore();
    const [chatSessionId, setChatSessionId] = useState(null);
    const [taskStatus, setTaskStatus] = useState(null);
    const [openModules, setOpenModules] = useState([]);
    useEffect(() => {
        let timer = null;
        const fetchOnce = () => Promise.all([
            api.get(`/reports/${id}`).catch(() => ({ data: null })),
            api.get(`/interpretations/${id}`).catch(() => ({ data: null })),
        ]).then(([r, i]) => {
            setReport(r.data);
            setInterpretation(i.data);
            const taskId = r.data?.task_id;
            if (taskId) {
                api.get(`/reports/tasks/${taskId}`).then(t => setTaskStatus(t.data?.status)).catch(() => { });
            }
            return { taskStatus: r.data?.task_status, interpStatus: i.data?.status };
        });
        const poll = async () => {
            await fetchOnce().then(({ taskStatus: ts, interpStatus: is }) => {
                const tState = ts || taskStatus;
                const interpDone = is === 'completed' || is === 'failed';
                const taskDone = tState === 'completed' || tState === 'failed';
                if (!taskDone || !interpDone) {
                    timer = setTimeout(poll, 10000);
                }
            });
        };
        fetchOnce().finally(() => setLoading(false));
        poll();
        return () => { if (timer)
            clearTimeout(timer); };
    }, [id]);
    useEffect(() => {
        if (!id)
            return;
        api.get('/chat/sessions').then(r => {
            const sessions = r.data || [];
            const existing = sessions.find((s) => s.report_id === Number(id));
            if (existing) {
                setChatSessionId(existing.id);
                chatStore.setCurrentSession(existing.id);
            }
            else {
                api.post('/chat/sessions', { report_id: Number(id) }).then(r2 => {
                    setChatSessionId(r2.data.id);
                    chatStore.setCurrentSession(r2.data.id);
                }).catch(() => { });
            }
        }).catch(() => { });
    }, [id]);
    useEffect(() => {
        setOpenModules([]);
    }, [id]);
    if (loading)
        return _jsx("div", { style: { textAlign: 'center', padding: 80 }, children: _jsx(Spin, { size: "large" }) });
    if (!report)
        return _jsx(Layout, { title: "\u62A5\u544A\u8BE6\u60C5", children: _jsx("p", { children: "\u62A5\u544A\u4E0D\u5B58\u5728" }) });
    // 统一用 ReportCard 一致的 effectiveStatus 计算,避免首页/详情页状态显示不一致
    const displayStatus = (() => {
        const ts = taskStatus || report?.task_status;
        const is = interpretation?.status;
        if (ts === 'failed' || is === 'failed')
            return 'failed';
        if (ts && ts !== 'completed')
            return ts;
        if (!is)
            return 'processing';
        if (is === 'completed')
            return 'completed';
        return is; // processing / pending
    })();
    const isProcessing = displayStatus !== 'completed' && displayStatus !== 'failed';
    const interpLoading = isProcessing;
    if (isProcessing) {
        return (_jsx(Layout, { title: "\u62A5\u544A\u8BE6\u60C5", children: _jsxs("div", { style: { textAlign: 'center', padding: '80px 20px' }, children: [_jsx(Spin, { size: "large" }), _jsx("h3", { style: { marginTop: 24, marginBottom: 8 }, children: "\u62A5\u544A\u5904\u7406\u4E2D" }), _jsx("p", { style: { color: '#888', marginBottom: 16 }, children: "AI \u6B63\u5728\u89E3\u6790\u8FD9\u4EFD\u62A5\u544A\uFF0C\u8BF7\u7A0D\u540E\u56DE\u6765\u67E5\u770B" }), _jsx(StatusTag, { status: displayStatus }), _jsx("div", { style: { marginTop: 32 }, children: _jsx(Button, { onClick: () => nav('/'), children: "\u8FD4\u56DE\u9996\u9875" }) })] }) }));
    }
    const overallLevel = interpretation?.overall_level;
    const rawIndicators = interpretation?.indicators?.length ? interpretation.indicators : (report?.indicators || []);
    const moduleOrder = interpretation?.module_order ?? report?.module_order;
    const { groups, flat } = toGroups(rawIndicators, moduleOrder);
    const totalCount = rawIndicators.length;
    return (_jsxs(Layout, { title: report.name || '报告详情', children: [_jsxs("div", { style: { display: 'flex', justifyContent: 'space-between', marginBottom: 20 }, children: [_jsxs("button", { onClick: () => nav(-1), style: {
                            border: 'none', background: 'none', fontSize: 14, color: 'var(--color-primary)',
                            cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
                        }, children: [_jsx(ArrowLeftOutlined, {}), " \u8FD4\u56DE"] }), _jsx(Popconfirm, { title: "\u786E\u5B9A\u5220\u9664\u8FD9\u4EFD\u62A5\u544A\u5417\uFF1F", description: "\u5220\u9664\u540E\u5C06\u65E0\u6CD5\u6062\u590D", onConfirm: async () => {
                            try {
                                await api.delete(`/reports/${id}`);
                                message.success('已删除');
                                nav('/');
                            }
                            catch {
                                message.error('删除失败');
                            }
                        }, okText: "\u5220\u9664", cancelText: "\u53D6\u6D88", okButtonProps: { danger: true }, children: _jsxs("button", { style: { border: 'none', background: 'none', fontSize: 14, color: '#ff4d4f', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }, children: [_jsx(DeleteOutlined, {}), " \u5220\u9664"] }) })] }), _jsx("div", { style: {
                    background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
                    padding: 20, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 20,
                }, children: _jsxs("div", { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }, children: [_jsxs("div", { children: [_jsx("div", { style: { fontSize: 16, fontWeight: 600 }, children: report.name || '未识别' }), _jsxs("div", { style: { fontSize: 13, color: 'var(--color-text-secondary)' }, children: [report.gender, " \u00B7 ", report.age, "\u5C81 \u00B7 ", report.report_date] })] }), _jsxs("div", { style: { display: 'flex', gap: 8 }, children: [overallLevel && _jsx(ColorBadge, { level: overallLevel, size: "md" }), _jsx(StatusTag, { status: displayStatus })] })] }) }), interpretation && (_jsxs("div", { style: {
                    display: 'flex', gap: 8, marginBottom: 16, padding: '12px 16px', background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)',
                }, children: [_jsxs("span", { style: { color: 'var(--color-red)', fontWeight: 600, fontSize: 13 }, children: ["\u7EA2\u533A ", interpretation.red_count] }), _jsx("span", { style: { color: 'var(--color-border)' }, children: "|" }), _jsxs("span", { style: { color: 'var(--color-yellow)', fontWeight: 600, fontSize: 13 }, children: ["\u9EC4\u533A ", interpretation.yellow_count] }), _jsx("span", { style: { color: 'var(--color-border)' }, children: "|" }), _jsxs("span", { style: { color: 'var(--color-green)', fontWeight: 600, fontSize: 13 }, children: ["\u7EFF\u533A ", interpretation.green_count] })] })), _jsxs("div", { style: { background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', padding: '0 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)' }, children: [groups.length > 0 && (_jsx(Collapse, { bordered: false, ghost: true, expandIconPosition: "end", activeKey: openModules, onChange: (keys) => setOpenModules((Array.isArray(keys) ? keys : [keys])), items: groups.map(({ name, items }) => {
                            const cnt = countLevels(items);
                            return {
                                key: name,
                                label: (_jsxs("span", { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', paddingRight: 8 }, children: [_jsx("span", { style: { fontSize: 14, fontWeight: 600 }, children: name }), _jsxs("span", { style: { fontSize: 12, color: 'var(--color-text-secondary)', display: 'flex', alignItems: 'center', gap: 8 }, children: [_jsxs("span", { children: [items.length, "\u9879"] }), cnt.red > 0 && _jsxs("span", { style: { color: 'var(--color-red)', fontWeight: 600 }, children: ["\u7EA2\u533A ", cnt.red] }), cnt.yellow > 0 && _jsxs("span", { style: { color: 'var(--color-yellow)', fontWeight: 600 }, children: ["\u9EC4\u533A ", cnt.yellow] }), cnt.green > 0 && _jsxs("span", { style: { color: 'var(--color-green)', fontWeight: 600 }, children: ["\u7EFF\u533A ", cnt.green] })] })] })),
                                children: items.map((ind, idx) => (_jsx(IndicatorRow, { item_name: ind.item_name, result_value: ind.result_value, unit: ind.unit, ref_range_low: ind.ref_range_low, ref_range_high: ind.ref_range_high, color_level: ind.color_level }, idx))),
                            };
                        }) })), flat.length > 0 && (_jsx("div", { style: { borderTop: groups.length > 0 ? '1px solid var(--color-border-light)' : 'none', marginTop: groups.length > 0 ? 8 : 0 }, children: flat.map((ind, idx) => (_jsx(IndicatorRow, { item_name: ind.item_name, result_value: ind.result_value, unit: ind.unit, ref_range_low: ind.ref_range_low, ref_range_high: ind.ref_range_high, color_level: ind.color_level }, idx))) })), totalCount === 0 && (_jsx("div", { style: { textAlign: 'center', padding: 32, color: 'var(--color-text-secondary)', fontSize: 13 }, children: "\u6682\u65E0\u6307\u6807\u6570\u636E" }))] }), interpretation?.status === 'completed' && (_jsx(ComparisonCard, { reportId: Number(id) })), _jsx(InterpretationReportCard, { summaries: interpretation?.summaries, references: interpretation?.references, loading: interpLoading, qualityNote: interpretation?.quality_note }), chatSessionId && (_jsxs("div", { style: { marginTop: 24, borderTop: '1px solid #E5E7EB', paddingTop: 16 }, children: [_jsx("div", { style: { fontWeight: 600, marginBottom: 8, fontSize: 14, color: '#0D9488' }, children: "\uD83D\uDCAC AI \u5065\u5EB7\u54A8\u8BE2\uFF08\u57FA\u4E8E\u672C\u62A5\u544A\uFF09" }), _jsx(ChatPanel, { sessionId: chatSessionId, placeholder: "\u57FA\u4E8E\u672C\u62A5\u544A\u63D0\u95EE...", compact: true })] }))] }));
}
