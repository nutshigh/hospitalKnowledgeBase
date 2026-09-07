import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { Typography, Popover } from 'antd';
export default function ChatBubble({ role, content, knowledgeRefs, streaming, structured }) {
    const isUser = role === 'user';
    const [dropdownOpen, setDropdownOpen] = useState(false);
    // 从 structured.citations（实时流）或 knowledge_refs（历史消息）提取引用
    const citations = structured?.citations?.length
        ? structured.citations
        : (knowledgeRefs || []).map((r, i) => ({
            ref_id: r.ref_id ?? i + 1,
            entry_id: r.entry_id,
            title: r.title,
            source: r.source ?? 'document',
            content: r.content ?? '',
        }));
    const popoverContent = (_jsxs("div", { style: { width: 300, maxHeight: 300, overflowY: 'auto' }, children: [_jsx(Typography.Text, { strong: true, style: { fontSize: 13, marginBottom: 8, display: 'block' }, children: "\u53C2\u8003\u6765\u6E90" }), citations.length === 0 ? (_jsx(Typography.Text, { type: "secondary", style: { fontSize: 12 }, children: "\u6682\u65E0\u5F15\u7528\u6765\u6E90" })) : (citations.map((c) => (_jsxs("div", { style: {
                    marginBottom: 8, padding: 8, borderRadius: 6,
                    background: '#F0FDF4', fontSize: 12, lineHeight: 1.5,
                }, children: [_jsxs("div", { style: { marginBottom: 2 }, children: [_jsxs(Typography.Text, { strong: true, style: { color: '#166534' }, children: ["[", c.ref_id, "]"] }), _jsx(Typography.Text, { style: { marginLeft: 4, fontSize: 12 }, children: c.title })] }), c.source && (_jsxs(Typography.Text, { type: "secondary", style: { fontSize: 11 }, children: ["\u6765\u6E90\uFF1A", c.source === 'knowledge_graph' ? '医学知识图谱' : '知识库文档'] })), c.content && (_jsx(Typography.Text, { style: { fontSize: 11, display: 'block', marginTop: 2, color: '#666' }, children: c.content.length > 100 ? c.content.slice(0, 100) + '...' : c.content }))] }, c.ref_id)))), (structured?.certainty || citations.length > 0) && (_jsx("div", { style: { marginTop: 6, paddingTop: 6, borderTop: '1px solid #E5E7EB' }, children: _jsxs(Typography.Text, { style: { fontSize: 11, color: '#888' }, children: ["\u786E\u5B9A\u6027\uFF1A", certaintyLabel(structured?.certainty || 'probable')] }) }))] }));
    function certaintyLabel(c) {
        const map = {
            definite: '确定',
            probable: '可能',
            refused: '无法判断',
        };
        return map[c] || c;
    }
    return (_jsx("div", { style: {
            display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start',
            marginBottom: 12,
        }, children: _jsxs("div", { style: {
                maxWidth: '80%',
                background: isUser ? '#E5E7EB' : '#CCFBF1',
                borderRadius: 8,
                padding: '8px 12px',
                fontSize: 14,
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
            }, children: [_jsxs(Typography.Text, { style: { fontSize: 14 }, children: [content, streaming && _jsx("span", { style: {
                                display: 'inline-block', width: 6, height: 14,
                                background: '#0D9488', marginLeft: 2, verticalAlign: 'text-bottom',
                                animation: 'blink 1s infinite',
                            } })] }), !isUser && citations.length > 0 && (_jsx("div", { style: { marginTop: 8, borderTop: '1px solid #D1FAE5', paddingTop: 6, textAlign: 'right' }, children: _jsx(Popover, { content: popoverContent, title: null, trigger: "click", placement: "bottomRight", open: dropdownOpen, onOpenChange: setDropdownOpen, children: _jsxs("a", { style: { fontSize: 12, cursor: 'pointer', color: '#0D9488' }, children: ["\u53C2\u8003\u6765\u6E90", citations.length > 0 ? ` (${citations.length})` : ''] }) }) })), !isUser && citations.length === 0 && knowledgeRefs && knowledgeRefs.length > 0 && (_jsx("div", { style: { marginTop: 8, borderTop: '1px solid #D1FAE5', paddingTop: 6 }, children: _jsxs(Typography.Text, { type: "secondary", style: { fontSize: 11 }, children: ["\u53C2\u8003\uFF1A", knowledgeRefs.map(r => r.title).join('、')] }) }))] }) }));
}
