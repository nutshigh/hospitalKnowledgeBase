import { Routes, Route, Navigate } from 'react-router-dom';
import { useAdminStore } from './stores/adminStore';
import LoginPage from './pages/LoginPage';
import PlatformDashboard from './pages/PlatformDashboard';
import GroupAnalysisPage from './pages/group-analysis/GroupAnalysisPage';
import AppLayout from './components/AppLayout';

function AuthGuard({ children }: { children: React.ReactNode }) {
  if (!useAdminStore(s => s.token)) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export const AppRouter = () => (
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<AuthGuard><AppLayout /></AuthGuard>}>
      <Route path="/" element={<PlatformDashboard />} />
      <Route path="/group-analysis" element={<GroupAnalysisPage />} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);
