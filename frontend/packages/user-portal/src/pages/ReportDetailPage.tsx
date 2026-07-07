import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Spin, Button, Popconfirm, message } from 'antd';
import { ArrowLeftOutlined, DeleteOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import IndicatorRow from '../components/IndicatorRow';
import StatusTag from '../components/StatusTag';
import ChatPanel from '../components/ChatPanel';
import { useChatStore } from '../stores/chatStore';
import { InterpretationReportCard } from '@hospital/shared';

const COLOR_ORDER: Record<string, number> = { red: 0, yellow: 1, green: 2 };

export default function ReportDetailPage() {
  const { id } = useParams();
  const { api } = useUserStore();
  const nav = useNavigate();
  const [report, setReport] = useState<any>(null);
  const [interpretation, setInterpretation] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const chatStore = useChatStore();
  const [chatSessionId, setChatSessionId] = useState<number | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.get(`/reports/${id}`).catch(() => ({ data: null })),
      api.get(`/interpretations/${id}`).catch(() => ({ data: null })),
    ]).then(([r, i]) => {
      setReport(r.data);
      setInterpretation(i.data);
      const taskId = r.data?.task_id;
      if (taskId) {
        api.get(`/reports/tasks/${taskId}`).then(t => setTaskStatus(t.data?.status)).catch(() => {});
      }
    }).finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (!id) return;
    api.get('/chat/sessions').then(r => {
      const sessions = r.data || [];
      const existing = sessions.find((s: any) => s.report_id === Number(id));
      if (existing) {
        setChatSessionId(existing.id);
        chatStore.setCurrentSession(existing.id);
      } else {
        api.post('/chat/sessions', { report_id: Number(id) }).then(r2 => {
          setChatSessionId(r2.data.id);
          chatStore.setCurrentSession(r2.data.id);
        }).catch(() => {});
      }
    }).catch(() => {});
  }, [id]);

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>;
  if (!report) return <Layout title="报告详情"><p>报告不存在</p></Layout>;

  const isProcessing = taskStatus && taskStatus !== 'completed' && taskStatus !== 'failed';
  const interpLoading = !!isProcessing || (interpretation?.status && interpretation.status !== 'completed');

  if (isProcessing) {
    return (
      <Layout title="报告详情">
        <div style={{ textAlign: 'center', padding: '80px 20px' }}>
          <Spin size="large" />
          <h3 style={{ marginTop: 24, marginBottom: 8 }}>报告处理中</h3>
          <p style={{ color: '#888', marginBottom: 16 }}>AI 正在解析这份报告，请稍后回来查看</p>
          <StatusTag status={taskStatus!} />
          <div style={{ marginTop: 32 }}>
            <Button onClick={() => nav('/')}>返回首页</Button>
          </div>
        </div>
      </Layout>
    );
  }

  const overallLevel = interpretation?.overall_level;
  // 优先用 interpretation.indicators（含 color_level + unit + ref_range，已 Task 6 join），
  // 旧数据/未生成时退化为 report.indicators（无 color_level）
  const rawIndicators = interpretation?.indicators?.length ? interpretation.indicators : (report?.indicators || []);
  const sortedIndicators = [...rawIndicators].sort((a, b) =>
    (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));

  return (
    <Layout title={report.name || '报告详情'}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
        <button onClick={() => nav(-1)} style={{
          border: 'none', background: 'none', fontSize: 14, color: 'var(--color-primary)',
          cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <ArrowLeftOutlined /> 返回
        </button>
        <Popconfirm
          title="确定删除这份报告吗？"
          description="删除后将无法恢复"
          onConfirm={async () => {
            try { await api.delete(`/reports/${id}`); message.success('已删除'); nav('/'); }
            catch { message.error('删除失败'); }
          }}
          okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
        >
          <button style={{ border: 'none', background: 'none', fontSize: 14, color: '#ff4d4f', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
            <DeleteOutlined /> 删除
          </button>
        </Popconfirm>
      </div>

      <div style={{
        background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
        padding: 20, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 20,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{report.name || '未识别'}</div>
            <div style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
              {report.gender} · {report.age}岁 · {report.report_date}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {overallLevel && <ColorBadge level={overallLevel} size="md" />}
            {interpretation?.status && <StatusTag status={interpretation.status} />}
          </div>
        </div>
      </div>

      {interpretation && (
        <div style={{
          display: 'flex', gap: 8, marginBottom: 16, padding: '12px 16px', background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)',
        }}>
          <span style={{ color: 'var(--color-red)', fontWeight: 600, fontSize: 13 }}>红区 {interpretation.red_count}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-yellow)', fontWeight: 600, fontSize: 13 }}>黄区 {interpretation.yellow_count}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-green)', fontWeight: 600, fontSize: 13 }}>绿区 {interpretation.green_count}</span>
        </div>
      )}

      <div style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', padding: '0 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)' }}>
        {sortedIndicators.map((ind: any, idx: number) => (
          <IndicatorRow
            key={idx}
            item_name={ind.item_name}
            result_value={ind.result_value}
            unit={ind.unit}
            ref_range_low={ind.ref_range_low}
            ref_range_high={ind.ref_range_high}
            color_level={ind.color_level}
          />
        ))}
        {sortedIndicators.length === 0 && (
          <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-secondary)', fontSize: 13 }}>暂无指标数据</div>
        )}
      </div>

      <InterpretationReportCard
        summaries={interpretation?.summaries}
        references={interpretation?.references}
        loading={interpLoading}
        qualityNote={interpretation?.quality_note}
      />

      {chatSessionId && (
        <div style={{ marginTop: 24, borderTop: '1px solid #E5E7EB', paddingTop: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14, color: '#0D9488' }}>
            💬 AI 健康咨询（基于本报告）
          </div>
          <ChatPanel sessionId={chatSessionId} placeholder="基于本报告提问..." compact />
        </div>
      )}
    </Layout>
  );
}