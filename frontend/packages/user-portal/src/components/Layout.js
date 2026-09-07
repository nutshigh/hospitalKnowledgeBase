import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useNavigate, useLocation } from 'react-router-dom';
import { HomeOutlined, MessageOutlined, UserOutlined } from '@ant-design/icons';
const tabs = [
    { key: '/', label: '首页', icon: _jsx(HomeOutlined, {}) },
    { key: '/chat', label: 'AI咨询', icon: _jsx(MessageOutlined, {}) },
    { key: '/profile', label: '我的', icon: _jsx(UserOutlined, {}) },
];
export default function Layout({ children, title }) {
    const nav = useNavigate();
    const loc = useLocation();
    const isActive = (key) => {
        if (key === '/')
            return loc.pathname === '/';
        return loc.pathname.startsWith(key);
    };
    return (_jsxs("div", { style: { maxWidth: 480, margin: '0 auto', minHeight: '100vh', background: 'var(--color-surface)' }, children: [_jsx("header", { style: {
                    padding: '20px 24px', borderBottom: '1px solid var(--color-border-light)',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                }, children: _jsx("h2", { style: { fontSize: 20, fontWeight: 700 }, children: title || '体检报告' }) }), _jsx("main", { style: { padding: '16px 20px 80px' }, children: children }), _jsx("nav", { style: {
                    position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)',
                    width: '100%', maxWidth: 480,
                    display: 'flex', justifyContent: 'space-around', alignItems: 'center',
                    background: 'var(--color-surface)', borderTop: '1px solid var(--color-border-light)',
                    padding: '8px 0 env(safe-area-inset-bottom, 8px)',
                    zIndex: 100,
                }, children: tabs.map(t => (_jsxs("div", { onClick: () => nav(t.key), style: {
                        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
                        padding: '4px 24px', cursor: 'pointer',
                        color: isActive(t.key) ? 'var(--color-primary)' : 'var(--color-text-secondary)',
                        fontSize: 12, fontWeight: isActive(t.key) ? 600 : 400,
                        transition: 'color 0.2s',
                        userSelect: 'none',
                    }, children: [_jsx("span", { style: { fontSize: 20 }, children: t.icon }), _jsx("span", { children: t.label })] }, t.key))) })] }));
}
