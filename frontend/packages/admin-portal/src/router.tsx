import { Routes, Route, Navigate } from 'react-router-dom';
import { useAdminStore } from './stores/adminStore';
import LoginPage from './pages/LoginPage';
import PlatformDashboard from './pages/PlatformDashboard';
import GroupAnalysisPage from './pages/group-analysis/GroupAnalysisPage';

function AuthGuard({ children }: { children: React.ReactNode }) {
  if (!useAdminStore(s => s.token)) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export const AppRouter = () => (
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/" element={<AuthGuard><PlatformDashboard /></AuthGuard>} />
    <Route path="/group-analysis" element={<AuthGuard><GroupAnalysisPage /></AuthGuard>} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);
