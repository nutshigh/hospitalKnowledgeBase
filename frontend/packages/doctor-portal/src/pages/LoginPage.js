import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, message } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
export default function LoginPage() {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [loading, setLoading] = useState(false);
    const { api, setAuth } = useDoctorStore();
    const nav = useNavigate();
    const handleLogin = async () => {
        setLoading(true);
        try {
            const res = await api.post('/auth/login', { username, password });
            setAuth(res.data.access_token, res.data.user_id, res.data.role, res.data.hospital_id || '');
            message.success('登录成功');
            nav('/');
        }
        catch {
            message.error('用户名或密码错误');
        }
        finally {
            setLoading(false);
        }
    };
    return (_jsx("div", { style: { minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--color-bg)' }, children: _jsxs("div", { style: { width: 360, background: 'var(--color-surface)', borderRadius: 'var(--radius-lg)', padding: 40, boxShadow: 'var(--shadow-lg)' }, children: [_jsxs("div", { style: { textAlign: 'center', marginBottom: 32 }, children: [_jsx("span", { style: { fontSize: 32 }, children: "\uD83C\uDFE5" }), _jsx("h1", { style: { fontSize: 20, marginTop: 12 }, children: "\u533B\u751F\u5DE5\u4F5C\u53F0" }), _jsx("p", { style: { fontSize: 13, color: 'var(--color-text-secondary)' }, children: "AI \u4F53\u68C0\u62A5\u544A\u89E3\u8BFB\u7CFB\u7EDF" })] }), _jsx(Input, { size: "large", placeholder: "\u7528\u6237\u540D", value: username, onChange: e => setUsername(e.target.value), style: { marginBottom: 12 }, onPressEnter: handleLogin }), _jsx(Input.Password, { size: "large", placeholder: "\u5BC6\u7801", value: password, onChange: e => setPassword(e.target.value), style: { marginBottom: 20 }, onPressEnter: handleLogin }), _jsx(Button, { type: "primary", block: true, size: "large", loading: loading, onClick: handleLogin, style: { height: 44, background: 'var(--color-primary)', border: 'none', fontWeight: 600 }, children: "\u767B \u5F55" })] }) }));
}
