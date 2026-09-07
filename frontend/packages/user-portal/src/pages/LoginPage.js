import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, message } from 'antd';
import { useUserStore } from '../stores/userStore';
export default function LoginPage() {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [loading, setLoading] = useState(false);
    const { api, setAuth } = useUserStore();
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
    return (_jsx("div", { style: {
            minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            background: 'var(--color-surface)', padding: 24,
        }, children: _jsxs("div", { style: { width: '100%', maxWidth: 360 }, children: [_jsxs("div", { style: { textAlign: 'center', marginBottom: 48 }, children: [_jsx("div", { style: {
                                width: 56, height: 56, borderRadius: 16, background: 'var(--color-primary-light)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px',
                            }, children: _jsx("span", { style: { fontSize: 28 }, children: "\uD83C\uDFE5" }) }), _jsx("h1", { style: { fontSize: 24, marginBottom: 8 }, children: "\u4F53\u68C0\u62A5\u544A\u67E5\u8BE2" }), _jsx("p", { style: { color: 'var(--color-text-secondary)', fontSize: 14 }, children: "\u767B\u5F55\u67E5\u770B\u60A8\u7684\u4F53\u68C0\u62A5\u544A\u4E0EAI\u89E3\u8BFB" })] }), _jsx(Input, { size: "large", placeholder: "\u7528\u6237\u540D", value: username, onChange: (e) => setUsername(e.target.value), style: { marginBottom: 12, borderRadius: 'var(--radius-sm)' }, onPressEnter: handleLogin }), _jsx(Input.Password, { size: "large", placeholder: "\u5BC6\u7801", value: password, onChange: (e) => setPassword(e.target.value), style: { marginBottom: 24, borderRadius: 'var(--radius-sm)' }, onPressEnter: handleLogin }), _jsx(Button, { type: "primary", block: true, size: "large", loading: loading, onClick: handleLogin, style: {
                        height: 48, borderRadius: 'var(--radius-sm)', background: 'var(--color-primary)',
                        border: 'none', fontWeight: 600, fontSize: 15,
                    }, children: "\u767B \u5F55" })] }) }));
}
