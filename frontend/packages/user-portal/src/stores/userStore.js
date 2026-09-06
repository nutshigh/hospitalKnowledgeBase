import { create } from 'zustand';
import { createApiClient } from '@hospital/shared';
const getToken = () => localStorage.getItem('token');
export const useUserStore = create((set) => ({
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
