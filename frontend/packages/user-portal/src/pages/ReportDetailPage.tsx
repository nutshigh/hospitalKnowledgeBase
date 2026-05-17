import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Spin, Button } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import IndicatorRow from '../components/IndicatorRow';
import StatusTag from '../components/StatusTag';

export default function ReportDetailPage() {
  const { id } = useParams();
  const { api } = useUserStore();
  const nav = useNavigate();
  const [report, setReport] = useState<any>(null);
  const [interpretation, setInterpretation] = useState<any>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get(`/reports/${id}`).catch(() => ({ data: null })),
      api.get(`/interpretations/${id}`).catch(() => ({ data: null })),
    ]).then(([r, i]) => {
      setReport(r.data);
      setInterpretation(i.data);
    }).finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>;
  if (!report) return <Layout title="报告详情"><p>报告不存在</p></Layout>;

  const overallLevel = interpretation?.overall_level;
  const indicators = interpretation?.indicators || report?.indicators || [];

  return (
    <Layout title={report.name || '报告详情'}>
      <button onClick={() => nav(-1)} style={{
        border: 'none', background: 'none', fontSize: 14, color: 'var(--color-primary)',
        cursor: 'pointer', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 4,
      }}>
        <ArrowLeftOutlined /> 返回
      </button>

      {/* Info card */}
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
        {interpretation?.summary_text && (
          <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--color-text-secondary)', marginTop: 12, padding: '12px', background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)' }}>
            {interpretation.summary_text}
          </div>
        )}
      </div>

      {/* Count bar */}
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

      {/* Indicators */}
      <div style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', padding: '0 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)' }}>
        {indicators.map((ind: any, idx: number) => (
          <IndicatorRow
            key={idx}
            item_name={ind.item_name}
            result_value={ind.result_value}
            unit={ind.unit}
            ref_range_low={ind.ref_range_low}
            ref_range_high={ind.ref_range_high}
            color_level={ind.color_level}
            explanation={ind.explanation}
            expanded={expandedIdx === idx}
            onToggle={() => setExpandedIdx(expandedIdx === idx ? null : idx)}
          />
        ))}
        {indicators.length === 0 && (
          <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-secondary)', fontSize: 13 }}>暂无指标数据</div>
        )}
      </div>
    </Layout>
  );
}
