import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Card, Spin } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
export default function DashboardPage() {
    const { api } = useDoctorStore();
    const [data, setData] = useState(null);
    useEffect(() => {
        api.get('/statistics/dashboard?start_date=2024-01-01&end_date=2026-12-31').then(r => setData(r.data)).catch(() => { });
    }, []);
    return (_jsxs(DoctorLayout, { children: [_jsx("h2", { style: { marginBottom: 24 }, children: "\u5DE5\u4F5C\u53F0\u6982\u89C8" }), !data ? _jsx(Spin, {}) : (_jsx("div", { style: { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }, children: [
                    { label: '报告总数', value: data.total_reports, color: 'var(--color-primary)' },
                    { label: '红区报告', value: data.red_reports, color: 'var(--color-red)' },
                    { label: '黄区报告', value: data.yellow_reports, color: 'var(--color-yellow)' },
                    { label: '异常率', value: data.abnormal_rate + '%', color: 'var(--color-text)' },
                ].map(s => (_jsxs(Card, { style: { textAlign: 'center', border: '1px solid var(--color-border)' }, children: [_jsx("div", { style: { fontSize: 28, fontWeight: 700, color: s.color }, children: s.value ?? '-' }), _jsx("div", { style: { fontSize: 13, color: 'var(--color-text-secondary)', marginTop: 4 }, children: s.label })] }, s.label))) }))] }));
}
