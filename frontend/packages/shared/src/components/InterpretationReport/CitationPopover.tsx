import React, { useState } from "react";
import { Popover, Typography } from "antd";

export interface CitationRef {
  ref_id: number;
  entry_id: number | null;
  title: string;
  source: string;
  content?: string;
}

export function findRef(text: string, references: CitationRef[] = []): CitationRef[] {
  const ids: number[] = [];
  const re = /\[(\d+)\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    ids.push(Number(m[1]));
  }
  return references.filter(r => ids.includes(r.ref_id));
}

export default function CitationList({ references }: { references: CitationRef[] }) {
  const [open, setOpen] = useState(false);
  if (!references.length) return null;
  return (
    <div style={{ marginTop: 12, padding: 12, background: "#F0FDF4", borderRadius: 8 }}>
      <Typography.Text strong style={{ fontSize: 12, color: "#166534", display: "block", marginBottom: 6 }}>
        参考来源（{references.length}）
      </Typography.Text>
      {references.map(r => (
        <div key={r.ref_id} style={{ fontSize: 12, marginBottom: 4, lineHeight: 1.5 }}>
          <Typography.Text style={{ color: "#166534", fontWeight: 600 }}>[{r.ref_id}]</Typography.Text>{" "}
          <Typography.Text>{r.title}</Typography.Text>
          <Typography.Text type="secondary" style={{ marginLeft: 6, fontSize: 11 }}>
            {r.source === "knowledge_graph" ? "知识图谱" : "知识库"}
          </Typography.Text>
        </div>
      ))}
    </div>
  );
}
