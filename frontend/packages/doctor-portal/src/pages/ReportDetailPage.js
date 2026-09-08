import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Spin, Card, Tag, Table } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
import { InterpretationReportCard } from '@hospital/shared';
import FollowupPanel from '../components/FollowupPanel';
const COLORS = { red: 'red', yellow: 'gold', green: 'green' };
const DEVIATION_TXT = { high: '↑ 偏高', low: '↓ 偏低', normal: '正常' };
export default function ReportDetailPage() {
    const { id } = useParams();
    const { api } = useDoctorStore();
    const [report, setReport] = useState(null);
    const [interp, setInterp] = useState(null);
    useEffect(() => {
        let timer = null;
        const poll = () => {
            Promise.all([
                api.get(`/reports/${id}`).catch(() => ({ data: null })),
                api.get(`/interpretations/${id}`).catch(() => ({ data: null })),
            ]).then(([r, i]) => {
                setReport(r.data);
                setInterp(i.data);
                const interpDone = i.data?.status === 'completed' || i.data?.status === 'failed';
                const taskDone = !r.data?.task_status || r.data.task_status === 'completed' || r.data.task_status === 'failed';
                if (!taskDone || !interpDone) {
                    timer = setTimeout(poll, 10000);
                }
            });
        };
        poll();
        return () => { if (timer)
            clearTimeout(timer); };
    }, [id]);
    if (!report)
        return _jsx(DoctorLayout, { children: _jsx(Spin, {}) });
    const columns = [
        { title: '指标', dataIndex: 'item_name', key: 'item_name' },
        { title: '结果', dataIndex: 'result_value', key: 'result_value',
            render: (v, r) => _jsxs("span", { children: [v, " ", _jsx("span", { style: { color: '#888', fontSize: 12 }, children: r.unit })] }) },
        { title: '参考范围', key: 'ref',
            render: (_, r) => r.ref_range_low && r.ref_range_high ? `${r.ref_range_low}-${r.ref_range_high}` : '-' },
        { title: '色级', dataIndex: 'color_level', key: 'color_level',
            render: (c) => c ? _jsx(Tag, { color: COLORS[c], children: c }) : '-' },
        { title: '偏离', dataIndex: 'deviation', key: 'deviation',
            render: (d) => d ? _jsx("span", { children: DEVIATION_TXT[d] || d }) : '-' },
    ];
    const rawIndicators = interp?.indicators?.length
        ? interp.indicators
        : (report?.indicators || []);
    const sortedIndicators = [...rawIndicators].sort((a, b) => {
        const order = { red: 0, yellow: 1, green: 2 };
        return (order[a.color_level] ?? 3) - (order[b.color_level] ?? 3);
    });
    return (_jsxs(DoctorLayout, { children: [_jsx("h2", { style: { marginBottom: 16 }, children: report.name || '报告详情' }), _jsxs(Card, { style: { marginBottom: 16 }, children: [_jsxs("p", { children: ["\u6027\u522B: ", report.gender, " \u00B7 \u5E74\u9F84: ", report.age, " \u00B7 \u65E5\u671F: ", report.report_date] }), report.unit_name && _jsxs("p", { children: ["\u5355\u4F4D: ", report.unit_name] })] }), interp && (_jsxs("div", { style: { marginBottom: 12 }, children: [_jsxs(Tag, { color: "red", children: ["\u7EA2\u533A ", interp.red_count] }), _jsxs(Tag, { color: "gold", children: ["\u9EC4\u533A ", interp.yellow_count] }), _jsxs(Tag, { color: "green", children: ["\u7EFF\u533A ", interp.green_count] }), _jsxs("span", { style: { marginLeft: 8 }, children: ["\u6574\u4F53\u5224\u5B9A\uFF1A", _jsx(Tag, { color: COLORS[interp.overall_level], children: interp.overall_level })] })] })), _jsx(Card, { title: "\u6307\u6807\u660E\u7EC6", style: { marginBottom: 16 }, children: _jsx(Table, { columns: columns, dataSource: sortedIndicators.map((i, idx) => ({ ...i, key: idx })), pagination: { pageSize: 20 }, size: "small" }) }), _jsx(InterpretationReportCard, { summaries: interp?.summaries, references: interp?.references, loading: !interp || interp.status !== 'completed', qualityNote: interp?.quality_note }), interp?.status === 'completed' && _jsx(FollowupPanel, { reportId: Number(id) })] }));
}
