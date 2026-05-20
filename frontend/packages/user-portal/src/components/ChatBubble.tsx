import { Typography } from 'antd';

interface Props {
  role: 'user' | 'assistant';
  content: string;
  knowledgeRefs?: Array<{ entry_id: number; title: string }>;
  streaming?: boolean;
}

export default function ChatBubble({ role, content, knowledgeRefs, streaming }: Props) {
  const isUser = role === 'user';

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
        {!isUser && knowledgeRefs && knowledgeRefs.length > 0 && (
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
