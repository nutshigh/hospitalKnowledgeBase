import { Routes, Route, Navigate } from 'react-router-dom';
import { useUserStore } from './stores/userStore';
import LoginPage from './pages/LoginPage';
import HomePage from './pages/HomePage';
import UploadPage from './pages/UploadPage';
import ReportDetailPage from './pages/ReportDetailPage';
import ChatPage from './pages/ChatPage';
import ProfilePage from './pages/ProfilePage';

function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = useUserStore(s => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export const AppRouter = () => (
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/" element={<AuthGuard><HomePage /></AuthGuard>} />
    <Route path="/upload" element={<AuthGuard><UploadPage /></AuthGuard>} />
    <Route path="/report/:id" element={<AuthGuard><ReportDetailPage /></AuthGuard>} />
    <Route path="/chat" element={<AuthGuard><ChatPage /></AuthGuard>} />
    <Route path="/chat/:sessionId" element={<AuthGuard><ChatPage /></AuthGuard>} />
    <Route path="/profile" element={<AuthGuard><ProfilePage /></AuthGuard>} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);
