import { useState } from 'react';
import { Typography, Popover } from 'antd';

interface Citation {
  ref_id: number;
  entry_id: number | null;
  title: string;
  source: string;
  content?: string;
}

interface StructuredData {
  certainty: string;
  certainty_reason: string;
  citations: Citation[];
  annotated_text?: string;
}

interface Props {
  role: 'user' | 'assistant';
  content: string;
  knowledgeRefs?: Array<{ entry_id: number; title: string }>;
  streaming?: boolean;
  structured?: StructuredData;
}

export default function ChatBubble({ role, content, knowledgeRefs, streaming, structured }: Props) {
  const isUser = role === 'user';
  const [dropdownOpen, setDropdownOpen] = useState(false);

  // 从 structured.citations（实时流）或 knowledge_refs（历史消息）提取引用
  const citations: Citation[] = structured?.citations?.length
    ? structured.citations
    : (knowledgeRefs || []).map((r, i) => ({
        ref_id: (r as any).ref_id ?? i + 1,
        entry_id: r.entry_id,
        title: r.title,
        source: (r as any).source ?? 'document',
        content: (r as any).content ?? '',
      }));

  const popoverContent = (
    <div style={{ width: 300, maxHeight: 300, overflowY: 'auto' }}>
      <Typography.Text strong style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>
        参考来源
      </Typography.Text>
      {citations.length === 0 ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>暂无引用来源</Typography.Text>
      ) : (
        citations.map((c) => (
          <div key={c.ref_id} style={{
            marginBottom: 8, padding: 8, borderRadius: 6,
            background: '#F0FDF4', fontSize: 12, lineHeight: 1.5,
          }}>
            <div style={{ marginBottom: 2 }}>
              <Typography.Text strong style={{ color: '#166534' }}>[{c.ref_id}]</Typography.Text>
              <Typography.Text style={{ marginLeft: 4, fontSize: 12 }}>{c.title}</Typography.Text>
            </div>
            {c.source && (
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                来源：{c.source === 'knowledge_graph' ? '医学知识图谱' : '知识库文档'}
              </Typography.Text>
            )}
            {c.content && (
              <Typography.Text style={{ fontSize: 11, display: 'block', marginTop: 2, color: '#666' }}>
                {c.content.length > 100 ? c.content.slice(0, 100) + '...' : c.content}
              </Typography.Text>
            )}
          </div>
        ))
      )}
      {(structured?.certainty || citations.length > 0) && (
        <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid #E5E7EB' }}>
          <Typography.Text style={{ fontSize: 11, color: '#888' }}>
            确定性：{certaintyLabel(structured?.certainty || 'probable')}
          </Typography.Text>
        </div>
      )}
    </div>
  );

  function certaintyLabel(c: string): string {
    const map: Record<string, string> = {
      definite: '确定',
      probable: '可能',
      refused: '无法判断',
    };
    return map[c] || c;
  }

  return (
    <div style={{
      display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start',
      marginBottom: 12,
    }}>
      <div style={{
        maxWidth: '80%',
        background: isUser ? '#E5E7EB' : '#CCFBF1',
        borderRadius: 8,
        padding: '8px 12px',
        fontSize: 14,
        lineHeight: 1.6,
        whiteSpace: 'pre-wrap',
      }}>
        <Typography.Text style={{ fontSize: 14 }}>
          {content}
          {streaming && <span style={{
            display: 'inline-block', width: 6, height: 14,
            background: '#0D9488', marginLeft: 2, verticalAlign: 'text-bottom',
            animation: 'blink 1s infinite',
          }} />}
        </Typography.Text>
        {!isUser && citations.length > 0 && (
          <div style={{ marginTop: 8, borderTop: '1px solid #D1FAE5', paddingTop: 6, textAlign: 'right' }}>
            <Popover
              content={popoverContent}
              title={null}
              trigger="click"
              placement="bottomRight"
              open={dropdownOpen}
              onOpenChange={setDropdownOpen}
            >
              <a style={{ fontSize: 12, cursor: 'pointer', color: '#0D9488' }}>
                参考来源{citations.length > 0 ? ` (${citations.length})` : ''}
              </a>
            </Popover>
          </div>
        )}
        {!isUser && citations.length === 0 && knowledgeRefs && knowledgeRefs.length > 0 && (
          <div style={{ marginTop: 8, borderTop: '1px solid #D1FAE5', paddingTop: 6 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              参考：{knowledgeRefs.map(r => r.title).join('、')}
            </Typography.Text>
          </div>
        )}
      </div>
    </div>
  );
}
