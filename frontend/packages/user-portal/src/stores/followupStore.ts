import { create } from 'zustand';
import { useUserStore } from './userStore';

interface FollowupState {
  count: number;
  refresh: () => Promise<void>;
}

export const useFollowupStore = create<FollowupState>((set) => ({
  count: 0,
  refresh: async () => {
    const api = useUserStore.getState().api;
    if (!useUserStore.getState().token) {
      set({ count: 0 });
      return;
    }
    try {
      const r = await api.get('/notifications/unread-count');
      set({ count: r.data?.unread_count ?? 0 });
    } catch {
      // 401 由拦截器处理;其余网络错误保持原计数
    }
  },
}));
