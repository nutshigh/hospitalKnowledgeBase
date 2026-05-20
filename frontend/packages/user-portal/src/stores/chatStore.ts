import { create } from 'zustand';

interface Message {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  knowledge_refs?: Array<{ entry_id: number; title: string }>;
  streaming?: boolean;
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

  setSessions: (sessions: ChatSession[]) => void;
  setCurrentSession: (id: number | null) => void;
  setMessages: (messages: Message[]) => void;
  addMessage: (msg: Message) => void;
  appendToken: (token: string) => void;
  finishStreaming: () => void;
  setLoading: (loading: boolean) => void;
  setStreaming: (streaming: boolean) => void;
  removeLastAssistantMessage: () => void;
}

export const useChatStore = create<ChatStore>((set) => ({
  sessions: [],
  currentSessionId: null,
  messages: [],
  loading: false,
  streaming: false,

  setSessions: (sessions) => set({ sessions }),
  setCurrentSession: (id) => set({ currentSessionId: id }),
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
