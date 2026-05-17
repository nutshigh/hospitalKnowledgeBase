import { Routes, Route, Navigate } from "react-router-dom";
import DashboardPage from "./pages/DashboardPage";

export const AppRouter = () => (
  <Routes>
    <Route path="/" element={<DashboardPage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);
