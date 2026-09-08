import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Card, Slider, Spin } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
export default function DispatchPage() {
    const { api } = useDoctorStore();
    const [metrics, setMetrics] = useState(null);
    const [config, setConfig] = useState({});
    useEffect(() => {
        api.get('/dispatch/metrics/current').then(r => setMetrics(r.data));
        api.get('/dispatch/config').then(r => setConfig(r.data));
        // eslint-disable-next-line
    }, []);
    if (!metrics)
        return _jsx(DoctorLayout, { children: _jsx(Spin, {}) });
    return (_jsxs(DoctorLayout, { children: [_jsx("h2", { style: { marginBottom: 16 }, children: "\u8C03\u5EA6\u7BA1\u7406 & \u8D44\u6E90\u76D1\u63A7" }), _jsxs("div", { style: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 24 }, children: [_jsxs(Card, { title: "CPU", size: "small", children: [metrics.cpu_percent, "%"] }), _jsxs(Card, { title: "\u5185\u5B58", size: "small", children: [metrics.memory_percent, "%"] }), _jsxs(Card, { title: "\u961F\u5217\u79EF\u538B", size: "small", children: ["\u89E3\u6790: ", metrics.queue_depth_parsing, " / \u89E3\u8BFB: ", metrics.queue_depth_interpretation] })] }), _jsxs(Card, { title: "\u5E76\u53D1\u63A7\u5236", children: [_jsxs("div", { style: { marginBottom: 16 }, children: [_jsx("div", { style: { marginBottom: 4 }, children: "\u89E3\u6790 Worker \u6570" }), _jsx(Slider, { min: 1, max: 8, defaultValue: config.max_parsing_workers || 4, onChange: (v) => api.put('/dispatch/config', { max_parsing_workers: v }) })] }), _jsxs("div", { children: [_jsx("div", { style: { marginBottom: 4 }, children: "\u89E3\u8BFB Worker \u6570" }), _jsx(Slider, { min: 1, max: 4, defaultValue: config.max_interpretation_workers || 2, onChange: (v) => api.put('/dispatch/config', { max_interpretation_workers: v }) })] })] })] }));
}
