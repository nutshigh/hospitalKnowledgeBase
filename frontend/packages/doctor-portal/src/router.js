import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { Routes, Route, Navigate } from 'react-router-dom';
import { useDoctorStore } from './stores/doctorStore';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import ReportsPage from './pages/ReportsPage';
import ReportDetailPage from './pages/ReportDetailPage';
import HighRiskPage from './pages/HighRiskPage';
import KnowledgePage from './pages/KnowledgePage';
import TriageRulesPage from './pages/TriageRulesPage';
import HealthProfilePage from './pages/HealthProfilePage';
import CrossComparePage from './pages/CrossComparePage';
import TrendPage from './pages/TrendPage';
import ExportPage from './pages/ExportPage';
import DispatchPage from './pages/DispatchPage';
import BatchUploadPage from './pages/BatchUploadPage';
function AuthGuard({ children }) {
    if (!useDoctorStore(s => s.token))
        return _jsx(Navigate, { to: "/login", replace: true });
    return _jsx(_Fragment, { children: children });
}
function RoleGuard({ allow, children }) {
    const role = useDoctorStore(s => s.role);
    if (!allow.includes(role))
        return _jsx(Navigate, { to: "/", replace: true });
    return _jsx(_Fragment, { children: children });
}
export const AppRouter = () => (_jsxs(Routes, { children: [_jsx(Route, { path: "/login", element: _jsx(LoginPage, {}) }), _jsx(Route, { path: "/", element: _jsx(AuthGuard, { children: _jsx(DashboardPage, {}) }) }), _jsx(Route, { path: "/reports", element: _jsx(AuthGuard, { children: _jsx(ReportsPage, {}) }) }), _jsx(Route, { path: "/reports/:id", element: _jsx(AuthGuard, { children: _jsx(ReportDetailPage, {}) }) }), _jsx(Route, { path: "/high-risk", element: _jsx(AuthGuard, { children: _jsx(HighRiskPage, {}) }) }), _jsx(Route, { path: "/knowledge", element: _jsx(AuthGuard, { children: _jsx(KnowledgePage, {}) }) }), _jsx(Route, { path: "/triage-rules", element: _jsx(AuthGuard, { children: _jsx(TriageRulesPage, {}) }) }), _jsx(Route, { path: "/statistics/health-profile", element: _jsx(AuthGuard, { children: _jsx(HealthProfilePage, {}) }) }), _jsx(Route, { path: "/statistics/cross-compare", element: _jsx(AuthGuard, { children: _jsx(CrossComparePage, {}) }) }), _jsx(Route, { path: "/statistics/trend", element: _jsx(AuthGuard, { children: _jsx(TrendPage, {}) }) }), _jsx(Route, { path: "/statistics/export", element: _jsx(AuthGuard, { children: _jsx(ExportPage, {}) }) }), _jsx(Route, { path: "/dispatch", element: _jsx(AuthGuard, { children: _jsx(DispatchPage, {}) }) }), _jsx(Route, { path: "/batch", element: _jsx(AuthGuard, { children: _jsx(RoleGuard, { allow: ['admin'], children: _jsx(BatchUploadPage, {}) }) }) }), _jsx(Route, { path: "*", element: _jsx(Navigate, { to: "/", replace: true }) })] }));
