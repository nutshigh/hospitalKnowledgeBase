import { useEffect, useRef, useCallback } from 'react';
import { Spin } from 'antd';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';
import { useChatStream } from '../hooks/useChatStream';
import ChatBubble from './ChatBubble';
import ChatInput from './ChatInput';

interface Props {
  sessionId: number;
  placeholder?: string;
  compact?: boolean;
}

export default function ChatPanel({ sessionId, placeholder, compact }: Props) {
  const { api } = useUserStore();
  const store = useChatStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  const onToken = useCallback((token: string) => {
    store.appendToken(token);
  }, []);

  const onDone = useCallback(() => {
    store.finishStreaming();
  }, []);

  const onError = useCallback((_err: string) => {
    store.removeLastAssistantMessage();
    store.finishStreaming();
  }, []);

  const { send } = useChatStream({ onToken, onDone, onError });

  // Load messages when session changes
  useEffect(() => {
    if (!sessionId) return;
    store.setLoading(true);
    api.get(`/chat/sessions/${sessionId}/messages`)
      .then(r => store.setMessages(r.data || []))
      .catch(() => {})
      .finally(() => store.setLoading(false));
  }, [sessionId]);

  // Auto scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [store.messages]);

  const handleSend = (content: string) => {
    store.addMessage({ role: 'user', content });
    store.setStreaming(true);
    store.addMessage({ role: 'assistant', content: '', streaming: true });
    send(
      `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'}/chat/sessions/${sessionId}/messages`,
      content,
    );
  };

  const maxHeight = compact ? 280 : 'calc(100vh - 200px)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        flex: 1, overflowY: 'auto', padding: '0 4px',
        maxHeight, minHeight: 160,
      }}>
        {store.loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin size="small" /></div>
        ) : store.messages.length === 0 ? (
          <div style={{
            textAlign: 'center', padding: 40,
            color: 'var(--color-text-secondary)', fontSize: 13,
          }}>
            基于您的体检报告，我可以帮您解答健康疑问
          </div>
        ) : (
          store.messages.map((msg, i) => (
            <ChatBubble
              key={i}
              role={msg.role}
              content={msg.content}
              knowledgeRefs={msg.knowledgeRefs}
              streaming={msg.streaming}
            />
          ))
        )}
        <div ref={bottomRef} />
      </div>
      <ChatInput
        onSend={handleSend}
        disabled={store.streaming}
        placeholder={placeholder}
      />
    </div>
  );
}
