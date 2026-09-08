import { useEffect, useState } from 'react';
import { Card, Tag, Spin, Empty } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';

interface Q { id: number; question_type: string; question_text: string; options: string[]; answer: any; }

interface FollowupData {
  id: number; report_id: number; status: string; overall_level: string;
  generated_at: string | null; submitted_at: string | null; template_name: string | null;
  questions: Q[];
}

const COLOR: any = { red: 'red', yellow: 'gold', green: 'green' };

export default function FollowupPanel({ reportId }: { reportId: number }) {
  const { api } = useDoctorStore();
  const [data, setData] = useState<FollowupData | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let stopped = false;
    let first = true;
    const load = async () => {
      try {
        const r = await api.get(`/followup/by-report/${reportId}`);
        if (!stopped) setData(r.data);
      } catch {
        // 404 或无记录:保留已有数据;从未成功则保持 null(不占版面)
      } finally {
        if (!stopped && first) { first = false; setLoaded(true); }
      }
    };
    load();
    const timer = setInterval(load, 15000);
    return () => { stopped = true; clearInterval(timer); };
  }, [reportId, api]);

  if (!loaded) return <Card title="随访问卷" loading style={{ marginBottom: 16 }} />;
  if (!data) return null; // 未触发随访,不占版面

  const label = (q: Q) => q.answer !== null && q.answer !== undefined
    ? (Array.isArray(q.answer) ? (q.answer as string[]).join('、') : String(q.answer))
    : '—';

  return (
    <Card title="随访问卷" style={{ marginBottom: 16 }}>
      <div style={{ marginBottom: 12 }}>
        <Tag color={data.status === 'completed' ? 'green' : 'orange'}>
          {data.status === 'completed' ? '已填写' : '待填写'}
        </Tag>
        {data.overall_level && <Tag color={COLOR[data.overall_level]}>{data.overall_level}</Tag>}
        {data.submitted_at && <span style={{ fontSize: 12, color: '#888', marginLeft: 8 }}>提交于 {data.submitted_at}</span>}
      </div>
      {data.questions.length === 0 ? <Empty description="问卷为空" /> : data.questions.map(q => (
        <div key={q.id} style={{ padding: '6px 0', borderBottom: '1px solid #f0f0f0' }}>
          <div style={{ fontWeight: 500, fontSize: 13 }}>{q.question_text}</div>
          <div style={{ fontSize: 13, color: '#555', marginTop: 2 }}>{label(q)}</div>
        </div>
      ))}
    </Card>
  );
}
