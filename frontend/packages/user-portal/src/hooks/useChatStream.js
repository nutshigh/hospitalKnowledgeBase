import { useCallback, useRef } from 'react';
export function useChatStream({ onToken, onStructured, onDone, onError }) {
    const abortRef = useRef(null);
    const send = useCallback(async (url, content) => {
        abortRef.current = new AbortController();
        const token = localStorage.getItem('token') || '';
        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${token}`,
                },
                body: JSON.stringify({ content }),
                signal: abortRef.current.signal,
            });
            if (!response.ok) {
                onError('请求失败');
                return;
            }
            const reader = response.body?.getReader();
            if (!reader) {
                onError('无法读取响应流');
                return;
            }
            const decoder = new TextDecoder();
            let buffer = '';
            let currentEvent = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done)
                    break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';
                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        currentEvent = line.slice(7).trim();
                        continue;
                    }
                    if (line.startsWith('data: ')) {
                        const dataStr = line.slice(6);
                        try {
                            const data = JSON.parse(dataStr);
                            if (currentEvent === 'error') {
                                onError(data.message || 'AI 响应失败');
                                return;
                            }
                            if (currentEvent === 'structured') {
                                onStructured?.(data);
                                continue;
                            }
                            if (currentEvent === 'done') {
                                onDone(data);
                                return;
                            }
                            if (currentEvent === 'token' && data.content !== undefined) {
                                onToken(data.content);
                            }
                        }
                        catch {
                            // skip parse error
                        }
                    }
                }
            }
        }
        catch (err) {
            if (err.name !== 'AbortError') {
                onError('网络错误，请重试');
            }
        }
    }, [onToken, onStructured, onDone, onError]);
    const abort = useCallback(() => {
        abortRef.current?.abort();
    }, []);
    return { send, abort };
}
