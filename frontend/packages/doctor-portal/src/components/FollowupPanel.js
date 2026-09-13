import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Card, Tag, Empty } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
const COLOR = { red: 'red', yellow: 'gold', green: 'green' };
export default function FollowupPanel({ reportId }) {
    const { api } = useDoctorStore();
    const [data, setData] = useState(null);
    const [loaded, setLoaded] = useState(false);
    useEffect(() => {
        setData(null);
        setLoaded(false);
        let stopped = false;
        let first = true;
        const load = async () => {
            try {
                const r = await api.get(`/followup/by-report/${reportId}`);
                if (!stopped)
                    setData(r.data);
            }
            catch {
                // 404 或无记录:保留已有数据;从未成功则保持 null(不占版面)
            }
            finally {
                if (!stopped && first) {
                    first = false;
                    setLoaded(true);
                }
            }
        };
        load();
        const timer = setInterval(load, 15000);
        return () => { stopped = true; clearInterval(timer); };
    }, [reportId, api]);
    if (!loaded)
        return _jsx(Card, { title: "\u968F\u8BBF\u95EE\u5377", loading: true, style: { marginBottom: 16 } });
    if (!data)
        return null; // 未触发随访,不占版面
    const label = (q) => q.answer !== null && q.answer !== undefined
        ? (Array.isArray(q.answer) ? q.answer.join('、') : String(q.answer))
        : '—';
    return (_jsxs(Card, { title: "\u968F\u8BBF\u95EE\u5377", style: { marginBottom: 16 }, children: [_jsxs("div", { style: { marginBottom: 12 }, children: [_jsx(Tag, { color: data.status === 'completed' ? 'green' : 'orange', children: data.status === 'completed' ? '已填写' : '待填写' }), data.overall_level && _jsx(Tag, { color: COLOR[data.overall_level], children: data.overall_level }), data.submitted_at && _jsxs("span", { style: { fontSize: 12, color: '#888', marginLeft: 8 }, children: ["\u63D0\u4EA4\u4E8E ", data.submitted_at] })] }), data.questions.length === 0 ? _jsx(Empty, { description: "\u95EE\u5377\u4E3A\u7A7A" }) : data.questions.map(q => (_jsxs("div", { style: { padding: '6px 0', borderBottom: '1px solid #f0f0f0' }, children: [_jsx("div", { style: { fontWeight: 500, fontSize: 13 }, children: q.question_text }), _jsx("div", { style: { fontSize: 13, color: '#555', marginTop: 2 }, children: label(q) })] }, q.id)))] }));
}
