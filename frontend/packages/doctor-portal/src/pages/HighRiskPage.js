import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Table, Button, message } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
export default function HighRiskPage() {
    const { api } = useDoctorStore();
    const [data, setData] = useState([]);
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        api.get('/interpretations/high-risk/list').then(r => setData(r.data.items || [])).finally(() => setLoading(false));
        // eslint-disable-next-line
    }, []);
    const columns = [
        { title: '姓名', dataIndex: 'name', key: 'name' },
        { title: '单位', dataIndex: 'unit_name', key: 'unit_name' },
        { title: '红区指标数', dataIndex: 'red_count', key: 'red_count', render: (v) => _jsx("span", { style: { color: 'var(--color-red)', fontWeight: 700 }, children: v }) },
        { title: '日期', dataIndex: 'report_date', key: 'report_date' },
        { title: '操作', key: 'action', render: (_, r) => (_jsx(Button, { size: "small", type: "primary", onClick: () => message.info('复查通知已下发'), children: "\u4E0B\u53D1\u590D\u67E5\u901A\u77E5" })) },
    ];
    return (_jsxs(DoctorLayout, { children: [_jsx("h2", { style: { marginBottom: 16 }, children: "\u9AD8\u98CE\u9669\u4EBA\u7FA4\u770B\u677F \uD83D\uDEA8" }), _jsx(Table, { dataSource: data, columns: columns, loading: loading, rowKey: "report_id", style: { background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' } })] }));
}
