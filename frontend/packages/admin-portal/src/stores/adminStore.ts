import { create } from 'zustand';
import { createApiClient } from '@hospital/shared';

interface AdminState {
  token: string | null; api: ReturnType<typeof createApiClient>;
  setAuth: (token: string) => void; logout: () => void;
}

const getToken = () => localStorage.getItem('admin_token');

export const useAdminStore = create<AdminState>((set) => ({
  token: getToken(), api: createApiClient(getToken),
  setAuth: (token) => { localStorage.setItem('admin_token', token); set({ token }); },
  logout: () => { localStorage.removeItem('admin_token'); set({ token: null }); },
}));
