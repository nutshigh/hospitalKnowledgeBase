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
const getRole = () => localStorage.getItem('doctor_role') || '';
const getUserId = () => {
  const v = localStorage.getItem('doctor_user_id');
  return v == null ? null : Number(v);
};
const getHospitalId = () => localStorage.getItem('doctor_hospital_id') || null;

export const useDoctorStore = create<DoctorState>((set) => ({
  token: getToken(),
  userId: getUserId(),
  role: getRole(),
  hospitalId: getHospitalId(),
  api: createApiClient(getToken),
  hospitalName: '',
  sidebarCollapsed: false,
  setAuth: (token, userId, role, hospitalId) => {
    localStorage.setItem('doctor_token', token);
    localStorage.setItem('doctor_role', role);
    localStorage.setItem('doctor_user_id', String(userId));
    localStorage.setItem('doctor_hospital_id', hospitalId);
    set({ token, userId, role, hospitalId });
  },
  logout: () => {
    localStorage.removeItem('doctor_token');
    localStorage.removeItem('doctor_role');
    localStorage.removeItem('doctor_user_id');
    localStorage.removeItem('doctor_hospital_id');
    set({ token: null, userId: null, role: '', hospitalId: null });
  },
  toggleSidebar: () => set(s => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));