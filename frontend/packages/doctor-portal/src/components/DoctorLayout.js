import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect } from 'react';
import { Select } from 'antd';
import { useNavigate, useLocation } from 'react-router-dom';
import { useDoctorStore } from '../stores/doctorStore';
const MENU_BASE = [
    { key: '/', label: '工作台', icon: '📊' },
    { key: '/reports', label: '报告管理', icon: '📋' },
    { key: '/high-risk', label: '高风险人群', icon: '🚨' },
    { key: '/knowledge', label: '知识库管理', icon: '📚' },
    { key: '/triage-rules', label: '三色规则配置', icon: '🎯' },
    { key: '-', label: '—', icon: '' },
    { key: '/statistics/health-profile', label: '健康画像', icon: '📈' },
    { key: '/statistics/cross-compare', label: '多维对比', icon: '🔄' },
    { key: '/statistics/trend', label: '趋势分析', icon: '📉' },
    { key: '/statistics/export', label: '报表导出', icon: '📄' },
    { key: '-', label: '—', icon: '' },
    { key: '/dispatch', label: '调度管理', icon: '⚙️' },
];
const ADMIN_MENU = [
    { key: '/batch', label: '批量上传分发', icon: '📦' },
];
export default function DoctorLayout({ children }) {
    const nav = useNavigate();
    const loc = useLocation();
    const { logout, sidebarCollapsed, toggleSidebar, role, activeHospital, hospitals, loadHospitals, setHospital } = useDoctorStore();
    const MENU = role === 'admin' ? [...MENU_BASE.slice(0, MENU_BASE.length - 1), ...ADMIN_MENU, MENU_BASE[MENU_BASE.length - 1]] : MENU_BASE;
    useEffect(() => {
        if (hospitals.length === 0)
            loadHospitals();
    }, [hospitals.length, loadHospitals]);
    const curName = hospitals.find(h => h.hospital_id === activeHospital)?.hospital_name || activeHospital || '';
    return (_jsxs("div", { style: { display: 'flex', minHeight: '100vh' }, children: [_jsxs("aside", { style: {
                    width: sidebarCollapsed ? 56 : 'var(--sidebar-w)', background: 'var(--color-surface)',
                    borderRight: '1px solid var(--color-border)', position: 'fixed', top: 0, left: 0, bottom: 0,
                    display: 'flex', flexDirection: 'column', transition: 'width 0.2s', zIndex: 10,
                }, children: [_jsx("div", { style: { padding: '16px 20px', borderBottom: '1px solid var(--color-border)' }, children: sidebarCollapsed ? (_jsx("span", { style: { fontSize: 20 }, children: "\uD83C\uDFE5" })) : (_jsx("h2", { style: { fontSize: 16, fontWeight: 700 }, children: "\u533B\u751F\u5DE5\u4F5C\u53F0" })) }), _jsx("nav", { style: { flex: 1, padding: '8px 12px' }, children: MENU.map((item) => {
                            if (item.key === '-') {
                                return _jsx("div", { style: { height: 1, background: 'var(--color-border)', margin: '8px 0' } }, item.label);
                            }
                            const active = loc.pathname === item.key || (item.key !== '/' && loc.pathname.startsWith(item.key));
                            return (_jsxs("div", { onClick: () => nav(item.key), style: {
                                    display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 'var(--radius-sm)',
                                    cursor: 'pointer', fontSize: 13, fontWeight: active ? 600 : 400,
                                    color: active ? 'var(--color-primary)' : 'var(--color-text-secondary)',
                                    background: active ? 'var(--color-primary-light)' : 'transparent',
                                    marginBottom: 2, transition: '0.15s',
                                }, children: [_jsx("span", { style: { fontSize: 16 }, children: item.icon }), !sidebarCollapsed && _jsx("span", { children: item.label })] }, item.key));
                        }) }), !sidebarCollapsed && (_jsx("div", { style: { padding: '12px 20px', borderTop: '1px solid var(--color-border)' }, children: _jsx("div", { style: { fontSize: 12, color: 'var(--color-text-secondary)', cursor: 'pointer' }, onClick: () => { logout(); nav('/login'); }, children: "\u9000\u51FA\u767B\u5F55" }) }))] }), _jsxs("div", { style: { marginLeft: sidebarCollapsed ? 56 : 'var(--sidebar-w)', flex: 1, transition: 'margin-left 0.2s' }, children: [_jsxs("header", { style: {
                            padding: '16px 24px', background: 'var(--color-surface)',
                            borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', gap: 16,
                        }, children: [_jsx("button", { onClick: toggleSidebar, style: { border: 'none', background: 'none', cursor: 'pointer', fontSize: 18 }, children: "\u2630" }), _jsx("span", { style: { fontSize: 14, color: 'var(--color-text-secondary)', flex: 1 }, children: MENU.find(m => m.key === loc.pathname || (m.key !== '/' && loc.pathname.startsWith(m.key)))?.label || '' }), _jsxs("div", { style: { display: 'flex', alignItems: 'center', gap: 8 }, children: [curName && _jsxs("span", { style: { fontSize: 13, color: 'var(--color-text-secondary)' }, children: ["\uD83C\uDFE5 ", curName] }), hospitals.length > 1 && (_jsx(Select, { size: "small", value: activeHospital || undefined, style: { width: 160 }, options: hospitals.map(h => ({
                                            value: h.hospital_id,
                                            label: `${h.hospital_name}(${h.hospital_id})`,
                                        })), onChange: (v) => { setHospital(v); window.location.reload(); } }))] })] }), _jsx("main", { style: { padding: 24 }, children: children })] })] }));
}
