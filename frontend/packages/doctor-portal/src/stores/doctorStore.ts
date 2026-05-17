import { create } from 'zustand';
import { createApiClient } from '@hospital/shared';

interface DoctorState {
  token: string | null; userId: number | null; role: string; hospitalId: string | null;
  api: ReturnType<typeof createApiClient>;
  hospitalName: string;
  sidebarCollapsed: boolean;
  setAuth: (token: string, userId: number, role: string, hospitalId: string) => void;
  logout: () => void;
  toggleSidebar: () => void;
}

const getToken = () => localStorage.getItem('doctor_token');

export const useDoctorStore = create<DoctorState>((set) => ({
  token: getToken(), userId: null, role: '', hospitalId: null,
  api: createApiClient(getToken), hospitalName: '', sidebarCollapsed: false,
  setAuth: (token, userId, role, hospitalId) => {
    localStorage.setItem('doctor_token', token);
    set({ token, userId, role, hospitalId });
  },
  logout: () => {
    localStorage.removeItem('doctor_token');
    set({ token: null, userId: null, role: '', hospitalId: null });
  },
  toggleSidebar: () => set(s => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
