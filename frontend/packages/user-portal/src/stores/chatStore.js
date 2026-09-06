import { create } from 'zustand';
export const useChatStore = create((set, get) => ({
    sessions: [],
    currentSessionId: null,
    messages: [],
    loading: false,
    streaming: false,
    selectedReports: {},
    setSessions: (sessions) => set({ sessions }),
    setCurrentSession: (id) => set({ currentSessionId: id }),
    getSelectedReport: (sessionId) => {
        return get().selectedReports[sessionId] ?? null;
    },
    setSelectedReport: (sessionId, reportId) => set((state) => ({
        selectedReports: { ...state.selectedReports, [sessionId]: reportId },
    })),
    setMessages: (messages) => set({ messages }),
    addMessage: (msg) => set((state) => ({ messages: [...state.messages, msg] })),
    appendToken: (token) => set((state) => {
        const msgs = [...state.messages];
        const idx = msgs.length - 1;
        const last = msgs[idx];
        if (last && last.role === 'assistant' && last.streaming) {
            msgs[idx] = { ...last, content: last.content + token };
        }
        return { messages: msgs };
    }),
    setStructured: (data) => set((state) => {
        const msgs = [...state.messages];
        const idx = msgs.length - 1;
        const last = msgs[idx];
        if (last && last.role === 'assistant') {
            msgs[idx] = {
                ...last,
                structured: data,
                // 用带 [n] 标注的文本替换原始流式文本
                content: data.annotated_text || last.content,
                // 同步 knowledge_refs 以便 ChatBubble 渲染来源按钮
                knowledge_refs: data.citations?.length
                    ? data.citations.map((c) => ({ entry_id: c.entry_id ?? 0, title: c.title }))
                    : last.knowledge_refs,
            };
        }
        return { messages: msgs };
    }),
    finishStreaming: () => set((state) => {
        const msgs = [...state.messages];
        const idx = msgs.length - 1;
        const last = msgs[idx];
        if (last && last.role === 'assistant') {
            msgs[idx] = { ...last, streaming: false };
        }
        return { messages: msgs, streaming: false };
    }),
    setLoading: (loading) => set({ loading }),
    setStreaming: (streaming) => set({ streaming }),
    removeLastAssistantMessage: () => set((state) => {
        const msgs = [...state.messages];
        if (msgs.length > 0 && msgs[msgs.length - 1].role === 'assistant') {
            msgs.pop();
        }
        return { messages: msgs };
    }),
}));
