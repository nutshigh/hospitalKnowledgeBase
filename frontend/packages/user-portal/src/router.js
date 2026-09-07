import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { Routes, Route, Navigate } from 'react-router-dom';
import { useUserStore } from './stores/userStore';
import LoginPage from './pages/LoginPage';
import HomePage from './pages/HomePage';
import UploadPage from './pages/UploadPage';
import ReportDetailPage from './pages/ReportDetailPage';
import ChatPage from './pages/ChatPage';
import ProfilePage from './pages/ProfilePage';
function AuthGuard({ children }) {
    const token = useUserStore(s => s.token);
    if (!token)
        return _jsx(Navigate, { to: "/login", replace: true });
    return _jsx(_Fragment, { children: children });
}
export const AppRouter = () => (_jsxs(Routes, { children: [_jsx(Route, { path: "/login", element: _jsx(LoginPage, {}) }), _jsx(Route, { path: "/", element: _jsx(AuthGuard, { children: _jsx(HomePage, {}) }) }), _jsx(Route, { path: "/upload", element: _jsx(AuthGuard, { children: _jsx(UploadPage, {}) }) }), _jsx(Route, { path: "/report/:id", element: _jsx(AuthGuard, { children: _jsx(ReportDetailPage, {}) }) }), _jsx(Route, { path: "/chat", element: _jsx(AuthGuard, { children: _jsx(ChatPage, {}) }) }), _jsx(Route, { path: "/chat/:sessionId", element: _jsx(AuthGuard, { children: _jsx(ChatPage, {}) }) }), _jsx(Route, { path: "/profile", element: _jsx(AuthGuard, { children: _jsx(ProfilePage, {}) }) }), _jsx(Route, { path: "*", element: _jsx(Navigate, { to: "/", replace: true }) })] }));
