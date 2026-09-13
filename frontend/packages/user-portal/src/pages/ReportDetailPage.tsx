import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Spin, Button, Popconfirm, message, Collapse } from 'antd';
import { ArrowLeftOutlined, DeleteOutlined, DownOutlined, UpOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import IndicatorRow from '../components/IndicatorRow';
import StatusTag from '../components/StatusTag';
import ChatPanel from '../components/ChatPanel';
import { useChatStore } from '../stores/chatStore';
import { InterpretationReportCard } from '@hospital/shared';

const COLOR_ORDER: Record<string, number> = { red: 0, yellow: 1, green: 2 };
const CONCLUSION_TITLE_RE = /^(总检建议与结论|总检结论|医师建议|综合建议|健康指导|结论与建议)\s*\n*/;

function cleanConclusionText(text: string): string {
  return text.replace(CONCLUSION_TITLE_RE, '').trim();
}

function isConclusionIndicator(ind: any): boolean {
  return ind.source === 'conclusion' || (!ind.result_value && !ind.ref_range_low && !ind.ref_range_high);
}

function sortByColor(items: any[]): any[] {
  return [...items].sort((a, b) =>
    (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));
}

function toGroups(indicators: any[], moduleOrder?: string[]) {
  const hasOrder = Array.isArray(moduleOrder) && moduleOrder.length > 0;
  if (!hasOrder) {
    // 旧后端无 module_order → 退化为今天的整体平铺(红黄绿优先)
    return { groups: [], flat: sortByColor(indicators) };
  }
  const groups = new Map<string, any[]>();
  const flat: any[] = [];
  for (const ind of indicators) {
    if (ind.group && moduleOrder.includes(ind.group)) {
      if (!groups.has(ind.group)) groups.set(ind.group, []);
      groups.get(ind.group)!.push(ind);
    } else {
      flat.push(ind);
    }
  }
  return {
    groups: moduleOrder.filter((g: string) => groups.has(g))
      .map((name) => ({ name, items: sortByColor(groups.get(name)!) })),
    flat: sortByColor(flat),
  };
}

function countLevels(items: any[]): { red: number; yellow: number; green: number } {
  const c = { red: 0, yellow: 0, green: 0 };
  for (const it of items) {
    const l: string = it.color_level;
    if (l === 'red' || l === 'yellow' || l === 'green') c[l] += 1;
  }
  return c;
}

export default function ReportDetailPage() {
  const { id } = useParams();
  const { api } = useUserStore();
  const nav = useNavigate();
  const [report, setReport] = useState<any>(null);
  const [interpretation, setInterpretation] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [conclusionExpanded, setConclusionExpanded] = useState(false);
  const chatStore = useChatStore();
  const [chatSessionId, setChatSessionId] = useState<number | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  const [openModules, setOpenModules] = useState<string[]>([]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;

    const fetchOnce = () => Promise.all([
      api.get(`/reports/${id}`).catch(() => ({ data: null })),
      api.get(`/interpretations/${id}`).catch(() => ({ data: null })),
    ]).then(([r, i]) => {
      setReport(r.data);
      setInterpretation(i.data);
      const taskId = r.data?.task_id;
      if (taskId) {
        api.get(`/reports/tasks/${taskId}`).then(t => setTaskStatus(t.data?.status)).catch(() => {});
      }
      return { taskStatus: r.data?.task_status, interpStatus: i.data?.status };
    });

    const poll = async () => {
      await fetchOnce().then(({ taskStatus: ts, interpStatus: is }) => {
        const tState = ts || taskStatus;
        const interpDone = is === 'completed' || is === 'failed';
        const taskDone = tState === 'completed' || tState === 'failed';
        if (!taskDone || !interpDone) {
          timer = setTimeout(poll, 10000);
        }
      });
    };

    fetchOnce().finally(() => setLoading(false));
    poll();

    return () => { if (timer) clearTimeout(timer); };
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

  useEffect(() => {
    setOpenModules([]);
  }, [id]);

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>;
  if (!report) return <Layout title="报告详情"><p>报告不存在</p></Layout>;

  const displayStatus = (() => {
    const ts = taskStatus || report?.task_status;
    const is = interpretation?.status;
    if (ts === 'failed' || is === 'failed') return 'failed';
    if (ts && ts !== 'completed') return ts;
    if (!is) return 'processing';
    if (is === 'completed') return 'completed';
    return is;
  })();
  const isProcessing = displayStatus !== 'completed' && displayStatus !== 'failed';
  const interpLoading = isProcessing;

  if (isProcessing) {
    return (
      <Layout title="报告详情">
        <div style={{ textAlign: 'center', padding: '80px 20px' }}>
          <Spin size="large" />
          <h3 style={{ marginTop: 24, marginBottom: 8 }}>报告处理中</h3>
          <p style={{ color: '#888', marginBottom: 16 }}>AI 正在解析这份报告，请稍后回来查看</p>
          <StatusTag status={displayStatus} />
          <div style={{ marginTop: 32 }}>
            <Button onClick={() => nav('/')}>返回首页</Button>
          </div>
        </div>
      </Layout>
    );
  }

  const overallLevel = interpretation?.overall_level;
  const rawIndicators = interpretation?.indicators?.length ? interpretation.indicators : (report?.indicators || []);
  const moduleOrder = interpretation?.module_order ?? report?.module_order;

  // === STRATEGY:v2026-08-16-original-name-display 展示原始名 ===
  // 结论条目的 item_name 可能被归一化名/疾病名覆盖(如"窦性心律不齐"→"心律失常"),
  // explanation 列存的是报告原文名。展示与去重均以原始名(explanation||item_name)为准,
  // 让用户能从展示名直接对应回报告。
  // 回退: 删除 displayName 并恢复 ind.item_name / ind.explanation 的旧用法。
  const displayName = (ind: any) => ind.explanation || ind.item_name;
  // === END STRATEGY ===

  const regularIndicators = rawIndicators.filter((ind: any) => !isConclusionIndicator(ind));
  const displayIndicators = [...regularIndicators].sort((a, b) =>
    (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));

  const { groups, flat } = toGroups(displayIndicators, moduleOrder);
  const levelCounts = countLevels(regularIndicators);

  const conclusionText = report.conclusion_text ? cleanConclusionText(report.conclusion_text) : '';

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
            <StatusTag status={displayStatus} />
          </div>
        </div>
      </div>

      {interpretation && (
        <div style={{
          display: 'flex', gap: 8, marginBottom: 16, padding: '12px 16px', background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)',
        }}>
          <span style={{ color: 'var(--color-red)', fontWeight: 600, fontSize: 13 }}>红区 {levelCounts.red}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-yellow)', fontWeight: 600, fontSize: 13 }}>黄区 {levelCounts.yellow}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-green)', fontWeight: 600, fontSize: 13 }}>绿区 {levelCounts.green}</span>
        </div>
      )}

      <div style={{
        background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', padding: 16,
        boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
        marginBottom: 20,
      }}>
        <div
          onClick={() => setConclusionExpanded(!conclusionExpanded)}
          style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            cursor: 'pointer',
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
            📋 总检建议与结论
            {conclusionText && !conclusionExpanded && (
              <span style={{ fontWeight: 400, marginLeft: 8, fontSize: 12, color: 'var(--color-text-secondary)' }}>
                {conclusionText.slice(0, 40)}...
              </span>
            )}
          </span>
          {conclusionText ? (
            conclusionExpanded ? <UpOutlined style={{ fontSize: 12 }} /> : <DownOutlined style={{ fontSize: 12 }} />
          ) : null}
        </div>
        {conclusionExpanded && (
          <div style={{ marginTop: 12, fontSize: 14, lineHeight: 1.8, whiteSpace: 'pre-wrap', color: 'var(--color-text)' }}>
            {conclusionText || '未提取到结论'}
          </div>
        )}
      </div>

      <div style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', padding: '0 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)' }}>
        {groups.length > 0 && (
          <Collapse
            bordered={false}
            ghost
            expandIconPosition="end"
            activeKey={openModules}
            onChange={(keys) => setOpenModules((Array.isArray(keys) ? keys : [keys]) as string[])}
            items={groups.map(({ name, items }) => {
              const cnt = countLevels(items);
              return {
                key: name,
                label: (
                  <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', paddingRight: 8 }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{name}</span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span>{items.length}项</span>
                      {cnt.red > 0 && <span style={{ color: 'var(--color-red)', fontWeight: 600 }}>红区 {cnt.red}</span>}
                      {cnt.yellow > 0 && <span style={{ color: 'var(--color-yellow)', fontWeight: 600 }}>黄区 {cnt.yellow}</span>}
                      {cnt.green > 0 && <span style={{ color: 'var(--color-green)', fontWeight: 600 }}>绿区 {cnt.green}</span>}
                    </span>
                  </span>
                ),
                children: items.map((ind: any, idx: number) => (
                  <IndicatorRow
                    key={idx}
                    item_name={displayName(ind)}
                    result_value={ind.result_value}
                    unit={ind.unit}
                    ref_range_low={ind.ref_range_low}
                    ref_range_high={ind.ref_range_high}
                    color_level={ind.color_level}
                    is_conclusion={isConclusionIndicator(ind)}
                  />
                )),
              };
            })}
          />
        )}

        {flat.length > 0 && (
          <div style={{ borderTop: groups.length > 0 ? '1px solid var(--color-border-light)' : 'none', marginTop: groups.length > 0 ? 8 : 0 }}>
            {flat.map((ind: any, idx: number) => (
              <IndicatorRow
                key={idx}
                item_name={displayName(ind)}
                result_value={ind.result_value}
                unit={ind.unit}
                ref_range_low={ind.ref_range_low}
                ref_range_high={ind.ref_range_high}
                color_level={ind.color_level}
                is_conclusion={isConclusionIndicator(ind)}
              />
            ))}
          </div>
        )}

        {displayIndicators.length === 0 && (
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
