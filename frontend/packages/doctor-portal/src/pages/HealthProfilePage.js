import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { DatePicker, Card } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
export default function HealthProfilePage() {
    const { api } = useDoctorStore();
    const [data, setData] = useState(null);
    const [dates, setDates] = useState(['2024-01-01', '2026-12-31']);
    useEffect(() => {
        api.get(`/statistics/health-profile?start_date=${dates[0]}&end_date=${dates[1]}`).then(r => setData(r.data));
        // eslint-disable-next-line
    }, [dates]);
    return (_jsxs(DoctorLayout, { children: [_jsx("h2", { style: { marginBottom: 16 }, children: "\u5065\u5EB7\u753B\u50CF" }), _jsx("div", { style: { display: 'flex', gap: 12, marginBottom: 16 }, children: _jsx(DatePicker.RangePicker, { onChange: (_, s) => s && setDates([s[0], s[1]]) }) }), data?.top_diseases?.map((d, i) => (_jsx(Card, { size: "small", style: { marginBottom: 8 }, children: _jsxs("div", { style: { display: 'flex', justifyContent: 'space-between' }, children: [_jsx("span", { children: d.item_name }), _jsxs("span", { style: { color: d.color_level === 'red' ? 'var(--color-red)' : 'var(--color-yellow)', fontWeight: 600 }, children: [d.color_level, " \u2014 ", d.count, "\u4F8B"] })] }) }, i)))] }));
}
