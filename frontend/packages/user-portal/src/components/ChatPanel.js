import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useCallback } from 'react';
import { Spin } from 'antd';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';
import { useChatStream } from '../hooks/useChatStream';
import ChatBubble from './ChatBubble';
import ChatInput from './ChatInput';
export default function ChatPanel({ sessionId, placeholder, compact }) {
    const { api } = useUserStore();
    const store = useChatStore();
    const bottomRef = useRef(null);
    const onToken = useCallback((token) => {
        store.appendToken(token);
    }, []);
    const onStructured = useCallback((data) => {
        store.setStructured(data);
    }, []);
    const onDone = useCallback(() => {
        store.finishStreaming();
    }, []);
    const onError = useCallback((_err) => {
        store.removeLastAssistantMessage();
        store.finishStreaming();
    }, []);
    const { send } = useChatStream({ onToken, onStructured, onDone, onError });
    // Load messages when session changes
    useEffect(() => {
        if (!sessionId)
            return;
        store.setLoading(true);
        api.get(`/chat/sessions/${sessionId}/messages`)
            .then(r => store.setMessages(r.data || []))
            .catch(() => { })
            .finally(() => store.setLoading(false));
    }, [sessionId]);
    // Auto scroll to bottom
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [store.messages]);
    const handleSend = (content) => {
        store.addMessage({ role: 'user', content });
        store.setStreaming(true);
        store.addMessage({ role: 'assistant', content: '', streaming: true });
        send(`${import.meta.env.VITE_API_BASE_URL || '/api/v1'}/chat/sessions/${sessionId}/messages`, content);
    };
    const containerHeight = compact ? 280 : 'calc(100vh - 260px)';
    return (_jsxs("div", { style: { display: 'flex', flexDirection: 'column', height: containerHeight }, children: [_jsxs("div", { style: {
                    flex: 1, overflowY: 'auto', padding: '0 4px',
                    minHeight: 0,
                    display: 'flex', flexDirection: 'column',
                }, children: [store.loading ? (_jsx("div", { style: { textAlign: 'center', padding: 40, margin: 'auto' }, children: _jsx(Spin, { size: "small" }) })) : store.messages.length === 0 ? (_jsx("div", { style: {
                            margin: 'auto', color: 'var(--color-text-secondary)', fontSize: 13,
                            textAlign: 'center', maxWidth: 420, padding: '0 16px',
                        }, children: compact || store.getSelectedReport(sessionId)
                            ? '基于您的体检报告，我可以帮您解答健康疑问'
                            : '您可以关联体检报告以获取更精准解读，或直接向我咨询健康问题' })) : (store.messages.map((msg, i) => (_jsx(ChatBubble, { role: msg.role, content: msg.content, knowledgeRefs: msg.knowledge_refs, streaming: msg.streaming, structured: msg.structured }, i)))), _jsx("div", { ref: bottomRef })] }), _jsx(ChatInput, { onSend: handleSend, disabled: store.streaming, placeholder: placeholder })] }));
}
