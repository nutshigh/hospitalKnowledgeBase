import { Routes, Route, Navigate } from "react-router-dom";
import HomePage from "./pages/HomePage";

export const AppRouter = () => (
  <Routes>
    <Route path="/" element={<HomePage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);
