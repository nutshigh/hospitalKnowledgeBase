import { create } from 'zustand';
import { createApiClient } from '@hospital/shared';
const getToken = () => localStorage.getItem('doctor_token');
const getRole = () => localStorage.getItem('doctor_role') || '';
const getUserId = () => {
    const v = localStorage.getItem('doctor_user_id');
    return v == null ? null : Number(v);
};
const getHospitalId = () => localStorage.getItem('doctor_hospital_id') || null;
const getActiveHospital = () => localStorage.getItem('doctor_active_hospital') || null;
const apiClient = createApiClient(getToken);
apiClient.interceptors.request.use((config) => {
    const token = getToken();
    const st = useDoctorStore.getState();
    if (token && (st.role === 'doctor' || st.role === 'admin') && st.activeHospital) {
        config.headers['X-Hospital-Id'] = st.activeHospital;
    }
    return config;
});
export const useDoctorStore = create((set, get) => ({
    token: getToken(),
    userId: getUserId(),
    role: getRole(),
    hospitalId: getHospitalId(),
    api: apiClient,
    hospitalName: '',
    sidebarCollapsed: false,
    activeHospital: getActiveHospital() || getHospitalId(),
    hospitals: [],
    setAuth: (token, userId, role, hospitalId) => {
        localStorage.setItem('doctor_token', token);
        localStorage.setItem('doctor_role', role);
        localStorage.setItem('doctor_user_id', String(userId));
        localStorage.setItem('doctor_hospital_id', hospitalId);
        localStorage.setItem('doctor_active_hospital', hospitalId);
        set({ token, userId, role, hospitalId, activeHospital: hospitalId });
    },
    setHospital: (id) => {
        localStorage.setItem('doctor_active_hospital', id);
        set({ activeHospital: id });
    },
    loadHospitals: async () => {
        const { api, token } = get();
        if (!token)
            return;
        try {
            const r = await api.get('/tenants');
            set({ hospitals: (r.data?.items || []).map((x) => ({
                    hospital_id: x.hospital_id, hospital_name: x.hospital_name || x.hospital_id,
                })) });
        }
        catch { /* 401 由拦截器处理;网络失败保持空,不阻断页面 */ }
    },
    logout: () => {
        localStorage.removeItem('doctor_token');
        localStorage.removeItem('doctor_role');
        localStorage.removeItem('doctor_user_id');
        localStorage.removeItem('doctor_hospital_id');
        localStorage.removeItem('doctor_active_hospital');
        set({ token: null, userId: null, role: '', hospitalId: null,
            activeHospital: null, hospitals: [], hospitalName: '' });
    },
    toggleSidebar: () => set(s => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
