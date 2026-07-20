import React from "react";
import { Card, Spin, Alert, Tag, Typography } from "antd";
import MarkdownRenderer from "./MarkdownRenderer";
import CitationList, { CitationRef } from "./CitationPopover";

export interface InterpretationReportData {
  overall_summary: string;
  abnormal_focus: string;
  trend_note: string;
  suggestions: string;
  risk_alert: string;
}

export interface InterpretationReportCardProps {
  summaries: InterpretationReportData | null | undefined;
  references?: CitationRef[];
  loading?: boolean;
  qualityNote?: string | null;
}

const SECTIONS: { key: keyof InterpretationReportData; title: string; accent?: string }[] = [
  { key: "overall_summary", title: "整体评估" },
  { key: "abnormal_focus", title: "重点异常解读" },
  { key: "trend_note", title: "历年趋势" },
  { key: "suggestions", title: "健康建议" },
  { key: "risk_alert", title: "风险提示", accent: "red" },
];

const ACCENT_COLOR: Record<string, string> = {
  red: "#fff2f0",
  yellow: "#fffbe6",
  green: "#f6ffed",
};

export default function InterpretationReportCard({ summaries, references = [], loading, qualityNote }: InterpretationReportCardProps) {
  if (loading) {
    return (
      <Card title="AI 解读报告" style={{ marginTop: 20 }}>
        <div style={{ textAlign: "center", padding: 32 }}>
          <Spin tip="AI 解读生成中..." />
        </div>
      </Card>
    );
  }

  if (!summaries || !Object.values(summaries).some(v => v && v.trim())) {
    return (
      <Card title="AI 解读报告" style={{ marginTop: 20 }}>
        <Typography.Text type="secondary">暂无 AI 解读报告</Typography.Text>
      </Card>
    );
  }

  const refs = references || [];

  return (
    <Card
      title={<span>🩺 AI 解读报告</span>}
      style={{ marginTop: 20 }}
      extra={refs.length > 0 ? <Tag color="green">{refs.length} 个参考来源</Tag> : null}
    >
      {qualityNote && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`AI 解读质量审核建议：${qualityNote}`}
        />
      )}
      {SECTIONS.map(sec => {
        const content = summaries[sec.key];
        if (!content || !content.trim()) return null;
        const bg = sec.accent ? ACCENT_COLOR[sec.accent] : undefined;
        return (
          <div key={sec.key} style={{
            marginBottom: 14, padding: 12,
            background: bg || "var(--color-bg, #fafafa)",
            borderRadius: 8,
            border: sec.accent === "red" ? "1px solid #ffccc7" : "1px solid var(--color-border-light, #f0f0f0)",
          }}>
            <Typography.Text strong style={{ fontSize: 14, display: "block", marginBottom: 6 }}>
              {sec.title}
            </Typography.Text>
            <MarkdownRenderer>{content}</MarkdownRenderer>
          </div>
        );
      })}
      <CitationList references={refs} />
    </Card>
  );
}
