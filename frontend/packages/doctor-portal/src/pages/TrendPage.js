import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Input, Table } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
export default function TrendPage() {
    const { api } = useDoctorStore();
    const [indicator, setIndicator] = useState('空腹血糖（GLU）');
    const [data, setData] = useState(null);
    useEffect(() => {
        if (indicator)
            api.get(`/statistics/trend?indicator=${encodeURIComponent(indicator)}&years=5`).then(r => setData(r.data));
        // eslint-disable-next-line
    }, [indicator]);
    return (_jsxs(DoctorLayout, { children: [_jsx("h2", { style: { marginBottom: 16 }, children: "\u8D8B\u52BF\u5206\u6790" }), _jsx(Input, { placeholder: "\u8F93\u5165\u6307\u6807\u540D\u79F0", value: indicator, onChange: e => setIndicator(e.target.value), style: { width: 300, marginBottom: 16 } }), _jsx(Table, { dataSource: data?.trend || [], rowKey: "year", columns: [
                    { title: '年份', dataIndex: 'year' }, { title: '总数', dataIndex: 'total' },
                    { title: '异常率', dataIndex: 'abnormal_rate', render: (v) => `${v}%` },
                ], style: { background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' } })] }));
}
