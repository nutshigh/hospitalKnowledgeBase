import { create } from 'zustand';
import { createApiClient } from '@hospital/shared';

interface UserState {
  token: string | null;
  userId: number | null;
  role: string;
  hospitalId: string | null;
  api: ReturnType<typeof createApiClient>;
  setAuth: (token: string, userId: number, role: string, hospitalId: string) => void;
  logout: () => void;
}

const getToken = () => localStorage.getItem('token');

export const useUserStore = create<UserState>((set) => ({
  token: getToken(),
  userId: null,
  role: '',
  hospitalId: null,
  api: createApiClient(getToken),
  setAuth: (token, userId, role, hospitalId) => {
    localStorage.setItem('token', token);
    set({ token, userId, role, hospitalId });
  },
  logout: () => {
    localStorage.removeItem('token');
    set({ token: null, userId: null, role: '', hospitalId: null });
  },
}));
