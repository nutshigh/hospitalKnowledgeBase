import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Progress, Spin, Table, Tag, message } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
import BatchUploader from '../components/BatchUploader';
import BatchDetailPanel from '../components/BatchDetailPanel';
import { useBatchTracker } from '../hooks/useBatchTracker';
import { STATUS_COLOR } from '../types/batch';
const PAGE_SIZE = 20;
const fmtTime = (iso) => iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-';
export default function BatchUploadPage() {
    const { api } = useDoctorStore();
    const [rows, setRows] = useState([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [expandedCards, setExpandedCards] = useState(new Set());
    const reloadHistory = useCallback(async () => {
        setHistoryLoading(true);
        try {
            const { data } = await api.get('/reports/batches', {
                params: { page, page_size: PAGE_SIZE },
            });
            setRows(data.items || []);
            setTotal(data.total ?? 0);
        }
        catch {
            message.error('历史批次加载失败');
        }
        finally {
            setHistoryLoading(false);
        }
    }, [api, page]);
    const handleSettled = useCallback((b) => {
        if (b.status === 'completed')
            message.success('批量处理完成');
        else if (b.status === 'partial_failed')
            message.warning('部分文件失败,可在下方查看并重试');
        else if (b.status === 'cancelled')
            message.info('批次已取消');
        void reloadHistory();
    }, [reloadHistory]);
    const tracker = useBatchTracker(api, handleSettled);
    const { active, loading: activeLoading, error: activeError, wake } = tracker;
    const refresh = useCallback(() => {
        wake();
        void reloadHistory();
    }, [wake, reloadHistory]);
    const handleCreated = useCallback(() => { refresh(); }, [refresh]);
    const handleChanged = useCallback(() => { refresh(); }, [refresh]);
    useEffect(() => { void reloadHistory(); }, [reloadHistory]);
    const cancelBatch = async (bid) => {
        try {
            await api.post(`/reports/batches/${bid}/cancel`, {});
            refresh();
        }
        catch (err) {
            message.error(err?.response?.data?.detail || '取消失败');
        }
    };
    const toggleCard = (bid) => {
        setExpandedCards((prev) => {
            const next = new Set(prev);
            if (next.has(bid))
                next.delete(bid);
            else
                next.add(bid);
            return next;
        });
    };
    const activeIds = new Set(active.map((b) => b.id));
    const displayRows = rows.filter((r) => !activeIds.has(r.id));
    const nightProcessing = active.some((b) => b.status === 'parsing' || b.status === 'interpreting');
    const columns = [
        { title: '文件名', dataIndex: 'filename', key: 'filename', ellipsis: true,
            render: (v) => _jsx("span", { title: v, style: { display: 'block' }, children: v }) },
        { title: '状态', dataIndex: 'status', key: 'status', width: 130,
            render: (s) => _jsx(Tag, { color: STATUS_COLOR[s], children: s }) },
        { title: '解析', dataIndex: 'parsed_ok', key: 'parsed_ok', width: 70 },
        { title: '解读', dataIndex: 'interp_ok', key: 'interp_ok', width: 70 },
        { title: '失败', dataIndex: 'failed', key: 'failed', width: 70,
            render: (v) => (v > 0 ? _jsx("span", { style: { color: 'var(--color-red)' }, children: v }) : v) },
        { title: '总数', dataIndex: 'total', key: 'total', width: 70 },
        { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160,
            render: (v) => fmtTime(v) },
        { title: '完成时间', dataIndex: 'completed_at', key: 'completed_at', width: 160,
            render: (v) => fmtTime(v) },
    ];
    return (_jsx(DoctorLayout, { children: _jsxs("div", { style: { maxWidth: 1100, margin: '0 auto' }, children: [_jsx("h2", { style: { marginBottom: 24 }, children: "\uD83D\uDCE6 \u6279\u91CF\u4E0A\u4F20\u5206\u53D1" }), _jsxs("div", { style: {
                        border: '1px solid var(--color-border)', borderRadius: 12,
                        padding: 24, background: 'var(--color-surface)', marginBottom: 24,
                    }, children: [_jsx("h3", { style: { fontSize: 14, marginTop: 0 }, children: "\u4E0A\u4F20\u65B0\u6279\u6B21" }), _jsx(BatchUploader, { api: api, onCreated: handleCreated })] }), _jsxs("h3", { style: { fontSize: 14, marginBottom: 8 }, children: ["\u5904\u7406\u4E2D\u6279\u6B21 ", active.length > 0 && `(${active.length})`] }), activeLoading && _jsx("div", { style: { padding: 16 }, children: _jsx(Spin, {}) }), activeError && (_jsx(Alert, { type: "error", showIcon: true, style: { marginBottom: 16 }, message: "\u6D3B\u8DC3\u6279\u6B21\u52A0\u8F7D\u5931\u8D25", action: _jsx(Button, { size: "small", onClick: wake, children: "\u91CD\u8BD5" }) })), !activeLoading && !activeError && active.length === 0 && (_jsx("p", { style: { color: 'var(--color-text-secondary)', fontSize: 13, marginBottom: 16 }, children: "\u5F53\u524D\u65E0\u5904\u7406\u4E2D\u6279\u6B21" })), active.map((b) => {
                    const done = (b.interp_ok ?? 0) + (b.failed ?? 0);
                    const pct = b.total ? Math.min(100, Math.round((done / b.total) * 100)) : 0;
                    return (_jsxs("div", { style: {
                            border: '1px solid var(--color-border)', borderRadius: 8,
                            padding: '12px 16px', marginBottom: 12, background: 'var(--color-surface)',
                        }, children: [_jsxs("div", { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }, children: [_jsxs("div", { style: { minWidth: 0, flex: 1 }, children: [_jsxs("div", { style: { display: 'flex', gap: 8, alignItems: 'center' }, children: [_jsx("span", { style: { fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: b.filename }), _jsx(Tag, { color: STATUS_COLOR[b.status], children: b.status })] }), _jsx(Progress, { percent: pct, status: b.status === 'partial_failed' ? 'exception' : 'active', size: "small", style: { margin: '8px 0 4px', maxWidth: 520 } }), _jsxs("span", { style: { fontSize: 12, color: 'var(--color-text-secondary)' }, children: [done, "/", b.total, " \u6587\u4EF6 \u00B7 \u89E3\u6790 ", b.parsed_ok, " \u00B7 \u89E3\u8BFB ", b.interp_ok, " \u00B7 \u5931\u8D25 ", b.failed, ' · ', fmtTime(b.created_at)] })] }), _jsxs("div", { style: { display: 'flex', gap: 8 }, children: [b.failed > 0 && (_jsx(Button, { size: "small", onClick: () => toggleCard(b.id), children: expandedCards.has(b.id) ? '收起失败' : `失败文件 (${b.failed})` })), _jsx(Button, { size: "small", danger: true, onClick: () => cancelBatch(b.id), children: "\u53D6\u6D88" })] })] }), expandedCards.has(b.id) && b.failed > 0 && (_jsx("div", { style: { marginTop: 12 }, children: _jsx(BatchDetailPanel, { api: api, batchId: b.id, onChanged: handleChanged }) }))] }, b.id));
                }), nightProcessing && active.length > 0 && (_jsx("p", { style: { fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 16 }, children: "\u6279\u91CF\u4EFB\u52A1\u5728\u591C\u95F4 22:00\u201308:00 \u65F6\u6BB5\u5904\u7406,\u767D\u5929\u53EF\u80FD\u505C\u7559\u5728\u6B64\u72B6\u6001,\u5C5E\u6B63\u5E38\u3002" })), _jsxs("div", { style: {
                        border: '1px solid var(--color-border)', borderRadius: 12,
                        padding: 24, background: 'var(--color-surface)', marginTop: 24,
                    }, children: [_jsx("h3", { style: { fontSize: 14, marginTop: 0 }, children: "\u5386\u53F2\u6279\u6B21" }), _jsx(Table, { dataSource: displayRows, columns: columns, rowKey: "id", loading: historyLoading, size: "small", style: { background: 'var(--color-surface)', borderRadius: 8 }, pagination: {
                                current: page, pageSize: PAGE_SIZE, total, showSizeChanger: false,
                                onChange: (p) => setPage(p),
                            }, expandable: {
                                rowExpandable: (r) => (r.failed ?? 0) > 0,
                                expandedRowRender: (r) => (_jsx(BatchDetailPanel, { api: api, batchId: r.id, onChanged: handleChanged })),
                            }, locale: { emptyText: '暂无批次记录' } }), displayRows.length > 0 && (_jsx("p", { style: { fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 8 }, children: "\u5C55\u5F00\u542B\u5931\u8D25\u6587\u4EF6\u7684\u6279\u6B21\u53EF\u67E5\u770B\u5931\u8D25\u660E\u7EC6\u5E76\u91CD\u8BD5\u3002" }))] })] }) }));
}
