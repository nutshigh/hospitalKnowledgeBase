import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Spin, Button, Radio, Checkbox, Input, Tag, message } from 'antd';
import Layout from '../components/Layout';
import { useUserStore } from '../stores/userStore';

interface Question {
  id: number; question_type: 'single' | 'multiple' | 'text';
  question_text: string; options: string[]; is_required: boolean;
  answer: any; sort_order: number;
}

interface Detail {
  id: number; status: 'pending' | 'completed'; overall_level: string;
  template_name: string | null; questions: Question[];
  submitted_at: string | null;
}

export default function FollowUpQuestionnairePage() {
  const { id } = useParams();
  const { api } = useUserStore();
  const nav = useNavigate();
  const [data, setData] = useState<Detail | null>(null);
  const [answers, setAnswers] = useState<Record<number, any>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.get(`/followup/${id}`).then(r => {
      setData(r.data);
      const init: Record<number, any> = {};
      ((r.data.questions || []) as Question[]).forEach(q => {
        if (q.answer !== null && q.answer !== undefined) init[q.id] = q.answer;
      });
      setAnswers(init);
    }).catch(() => nav('/followup'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (!data) return <Layout title="随访问卷"><div style={{ textAlign: 'center', padding: 60 }}><Spin /></div></Layout>;

  const submit = async () => {
    const missing = (data.questions || []).find(q => q.is_required &&
      (answers[q.id] === undefined || answers[q.id] === null || answers[q.id] === '' ||
       (Array.isArray(answers[q.id]) && answers[q.id].length === 0)));
    if (missing) { message.warning(`请填写必答题:${missing.question_text}`); return; }
    setSubmitting(true);
    try {
      const list = (data.questions || []).map(q => ({
        question_id: q.id,
        answer: q.question_type === 'multiple' ? (answers[q.id] || []) : (answers[q.id] ?? ''),
      }));
      await api.post(`/followup/${data.id}/submit`, { answers: list });
      message.success('问卷已提交');
      nav('/followup');
    } catch (e: any) {
      message.error(e.response?.data?.detail || '提交失败');
    } finally { setSubmitting(false); }
  };

  const readonly = data.status === 'completed';

  return (
    <Layout title="随访问卷">
      <div style={{ marginBottom: 12 }}>
        <Tag color={data.overall_level === 'red' ? 'red' : 'gold'}>{data.overall_level}</Tag>
        {data.submitted_at && <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginLeft: 8 }}>已提交 {data.submitted_at}</span>}
      </div>
      {(data.questions || []).map(q => (
        <div key={q.id} style={{
          background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
          padding: '14px 16px', boxShadow: 'var(--shadow-sm)',
          border: '1px solid var(--color-border-light)', marginBottom: 12,
        }}>
          <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 8 }}>
            {q.is_required && <span style={{ color: 'var(--color-red)' }}>* </span>}
            {q.question_text}
          </div>
          {q.question_type === 'single' && (
            <Radio.Group disabled={readonly} value={answers[q.id]}
              onChange={e => setAnswers({ ...answers, [q.id]: e.target.value })}>
              {(q.options || []).map(o => <Radio key={o} value={o}>{o}</Radio>)}
            </Radio.Group>
          )}
          {q.question_type === 'multiple' && (
            <Checkbox.Group disabled={readonly} value={answers[q.id] || []}
              onChange={v => setAnswers({ ...answers, [q.id]: v })}>
              {(q.options || []).map(o => <Checkbox key={o} value={o}>{o}</Checkbox>)}
            </Checkbox.Group>
          )}
          {q.question_type === 'text' && (
            <Input.TextArea disabled={readonly} rows={3}
              value={answers[q.id] ?? ''}
              onChange={e => setAnswers({ ...answers, [q.id]: e.target.value })} />
          )}
        </div>
      ))}
      {!readonly && (
        <Button type="primary" block loading={submitting} onClick={submit}
          style={{ marginTop: 8 }}>提交问卷</Button>
      )}
    </Layout>
  );
}
