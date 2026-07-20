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

function AuthGuard({ children }: { children: React.ReactNode }) {
  if (!useDoctorStore(s => s.token)) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function RoleGuard({ allow, children }: { allow: string[]; children: React.ReactNode }) {
  const role = useDoctorStore(s => s.role);
  if (!allow.includes(role)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export const AppRouter = () => (
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/" element={<AuthGuard><DashboardPage /></AuthGuard>} />
    <Route path="/reports" element={<AuthGuard><ReportsPage /></AuthGuard>} />
    <Route path="/reports/:id" element={<AuthGuard><ReportDetailPage /></AuthGuard>} />
    <Route path="/high-risk" element={<AuthGuard><HighRiskPage /></AuthGuard>} />
    <Route path="/knowledge" element={<AuthGuard><KnowledgePage /></AuthGuard>} />
    <Route path="/triage-rules" element={<AuthGuard><TriageRulesPage /></AuthGuard>} />
    <Route path="/statistics/health-profile" element={<AuthGuard><HealthProfilePage /></AuthGuard>} />
    <Route path="/statistics/cross-compare" element={<AuthGuard><CrossComparePage /></AuthGuard>} />
    <Route path="/statistics/trend" element={<AuthGuard><TrendPage /></AuthGuard>} />
    <Route path="/statistics/export" element={<AuthGuard><ExportPage /></AuthGuard>} />
    <Route path="/dispatch" element={<AuthGuard><DispatchPage /></AuthGuard>} />
    <Route path="/batch" element={
      <AuthGuard>
        <RoleGuard allow={['admin']}>
          <BatchUploadPage />
        </RoleGuard>
      </AuthGuard>
    } />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);