import { create } from 'zustand';

import type { StructuredData } from '../hooks/useChatStream';

interface Message {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  knowledge_refs?: Array<{ entry_id: number; title: string }>;
  streaming?: boolean;
  structured?: StructuredData;
}

interface ChatSession {
  id: number;
  report_id: number | null;
  title: string | null;
  created_at: string;
  updated_at: string;
}

interface ChatStore {
  sessions: ChatSession[];
  currentSessionId: number | null;
  messages: Message[];
  loading: boolean;
  streaming: boolean;
  selectedReports: Record<number, number | null>;  // sessionId -> reportId

  setSessions: (sessions: ChatSession[]) => void;
  setCurrentSession: (id: number | null) => void;
  setMessages: (messages: Message[]) => void;
  addMessage: (msg: Message) => void;
  appendToken: (token: string) => void;
  setStructured: (data: StructuredData) => void;
  finishStreaming: () => void;
  setLoading: (loading: boolean) => void;
  setStreaming: (streaming: boolean) => void;
  removeLastAssistantMessage: () => void;
  getSelectedReport: (sessionId: number) => number | null;
  setSelectedReport: (sessionId: number, reportId: number | null) => void;
}

export const useChatStore = create<ChatStore>((set) => ({
  sessions: [],
  currentSessionId: null,
  messages: [],
  loading: false,
  streaming: false,

  selectedReports: {},

  setSessions: (sessions) => set({ sessions }),
  setCurrentSession: (id) => set({ currentSessionId: id }),
  getSelectedReport: (sessionId) => {
    return useChatStore.getState().selectedReports[sessionId] ?? null;
  },
  setSelectedReport: (sessionId, reportId) =>
    set((state) => ({
      selectedReports: { ...state.selectedReports, [sessionId]: reportId },
    })),
  setMessages: (messages) => set({ messages }),
  addMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg] })),
  appendToken: (token) =>
    set((state) => {
      const msgs = [...state.messages];
      const idx = msgs.length - 1;
      const last = msgs[idx];
      if (last && last.role === 'assistant' && last.streaming) {
        msgs[idx] = { ...last, content: last.content + token };
      }
      return { messages: msgs };
    }),
  setStructured: (data) =>
    set((state) => {
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
            ? data.citations.map((c: any) => ({ entry_id: c.entry_id ?? 0, title: c.title }))
            : last.knowledge_refs,
        };
      }
      return { messages: msgs };
    }),
  finishStreaming: () =>
    set((state) => {
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
  removeLastAssistantMessage: () =>
    set((state) => {
      const msgs = [...state.messages];
      if (msgs.length > 0 && msgs[msgs.length - 1].role === 'assistant') {
        msgs.pop();
      }
      return { messages: msgs };
    }),
}));
