import { Routes, Route, Navigate } from "react-router-dom";
import PlatformDashboard from "./pages/PlatformDashboard";

export const AppRouter = () => (
  <Routes>
    <Route path="/" element={<PlatformDashboard />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);
