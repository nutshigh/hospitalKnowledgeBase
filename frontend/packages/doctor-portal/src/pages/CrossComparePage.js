import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { DatePicker, Select, Table } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
export default function CrossComparePage() {
    const { api } = useDoctorStore();
    const [data, setData] = useState(null);
    const [dim, setDim] = useState('unit');
    const [dates, setDates] = useState(['2024-01-01', '2026-12-31']);
    useEffect(() => {
        api.get(`/statistics/cross-compare?start_date=${dates[0]}&end_date=${dates[1]}&x_dimension=${dim}`).then(r => setData(r.data));
        // eslint-disable-next-line
    }, [dates, dim]);
    return (_jsxs(DoctorLayout, { children: [_jsx("h2", { style: { marginBottom: 16 }, children: "\u591A\u7EF4\u4EA4\u53C9\u5BF9\u6BD4" }), _jsxs("div", { style: { display: 'flex', gap: 12, marginBottom: 16 }, children: [_jsx(DatePicker.RangePicker, { onChange: (_, s) => s && setDates([s[0], s[1]]) }), _jsx(Select, { value: dim, onChange: setDim, options: [{ value: 'unit', label: '按单位' }, { value: 'gender', label: '按性别' }, { value: 'age_group', label: '按年龄段' }] })] }), _jsx(Table, { dataSource: data?.data || [], rowKey: "label", columns: [
                    { title: '分组', dataIndex: 'label' }, { title: '总数', dataIndex: 'total' },
                    { title: '红区', dataIndex: 'red', render: (v) => _jsx("span", { style: { color: 'var(--color-red)', fontWeight: 600 }, children: v }) },
                    { title: '黄区', dataIndex: 'yellow', render: (v) => _jsx("span", { style: { color: 'var(--color-yellow)', fontWeight: 600 }, children: v }) },
                ], style: { background: 'var(--color-surface)', borderRadius: 'var(--radius-md)' } })] }));
}
