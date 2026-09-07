import { useEffect, useState } from "react";
import { Card, Input, Select, Checkbox, Button, Space, message, Typography } from "antd";
import { PlusOutlined, UpOutlined, DownOutlined, DeleteOutlined } from "@ant-design/icons";
import { getTemplate, saveTemplate, TemplateQuestion, FollowupTemplate } from "../api/followupTemplate";

const { Text } = Typography;
const TYPE_OPTIONS = [
  { value: "single", label: "单选" },
  { value: "multiple", label: "多选" },
  { value: "text", label: "文本填空" },
];

export default function FollowupTemplatePage() {
  const [tpl, setTpl] = useState<FollowupTemplate>({ id: null, name: "通用检后随访", description: null, questions: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getTemplate().then(t => {
      const base = t.id ? t : { ...t, name: "通用检后随访" };
      setTpl({ ...base, questions: (t.questions || []).length ? t.questions : [] });
    }).finally(() => setLoading(false));
  }, []);

  const patchQ = (idx: number, patch: Partial<TemplateQuestion>) => {
    setTpl(s => ({ ...s, questions: s.questions.map((q, i) => i === idx ? { ...q, ...patch } : q) }));
  };

  const add = () => setTpl(s => ({
    ...s,
    questions: [...s.questions, {
      question_type: "text", question_text: "", options: [],
      is_required: true, sort_order: s.questions.length, is_active: true,
    }],
  }));

  const remove = (idx: number) => setTpl(s => ({ ...s, questions: s.questions.filter((_, i) => i !== idx) }));

  const move = (idx: number, dir: -1 | 1) => setTpl(s => {
    const arr = [...s.questions];
    const j = idx + dir;
    if (j < 0 || j >= arr.length) return s;
    [arr[idx], arr[j]] = [arr[j], arr[idx]];
    arr.forEach((q, i) => { q.sort_order = i; });
    return { ...s, questions: arr };
  });

  const save = async () => {
    if (!tpl.questions.length) { message.error("激活模板不允许空题目"); return; }
    const bad = tpl.questions.findIndex(q =>
      !q.question_text.trim() ||
      ((q.question_type === "single" || q.question_type === "multiple") &&
        (!q.options || q.options.filter(o => o.trim()).length === 0)));
    if (bad >= 0) { message.error(`第 ${bad + 1} 题缺少题干或选项`); return; }
    setSaving(true);
    try {
      const saved = await saveTemplate({
        ...tpl,
        questions: tpl.questions.map((q, i) => ({ ...q, sort_order: i, options: q.question_type === "text" ? [] : (q.options || []).filter(o => o.trim()) })),
      });
      setTpl(saved);
      message.success("模板已保存,后续新解读将套用");
    } catch (e: any) {
      message.error(e.response?.data?.detail || "保存失败");
    } finally { setSaving(false); }
  };

  if (loading) return <div style={{ textAlign: "center", padding: 48 }}>加载中…</div>;

  return (
    <div style={{ maxWidth: 900, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>随访问卷模板</h2>
          <Text type="secondary">平台统一维护的单套激活模板;红/黄报告解读完成后按此快照生成问卷</Text>
        </div>
        <Button type="primary" loading={saving} onClick={save}>保存模板</Button>
      </div>

      <Card>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space>
            <span>模板名:</span>
            <Input style={{ width: 240 }} value={tpl.name}
              onChange={e => setTpl({ ...tpl, name: e.target.value })} />
          </Space>
          {tpl.questions.map((q, idx) => (
            <div key={idx} style={{ border: "1px solid #f0f0f0", borderRadius: 8, padding: 12, background: "#FAFAF8" }}>
              <Space style={{ marginBottom: 8 }}>
                <Button size="small" icon={<UpOutlined />} onClick={() => move(idx, -1)} />
                <Button size="small" icon={<DownOutlined />} onClick={() => move(idx, 1)} />
                <Select size="small" value={q.question_type} style={{ width: 110 }}
                  options={TYPE_OPTIONS}
                  onChange={v => patchQ(idx, { question_type: v, options: v === "text" ? [] : q.options })} />
                <Checkbox checked={q.is_required} onChange={e => patchQ(idx, { is_required: e.target.checked })}>必填</Checkbox>
                <Checkbox checked={q.is_active} onChange={e => patchQ(idx, { is_active: e.target.checked })}>启用</Checkbox>
                <Button size="small" danger icon={<DeleteOutlined />} onClick={() => remove(idx)} />
              </Space>
              <Input placeholder="题干(如:近期是否头晕?)" value={q.question_text}
                onChange={e => patchQ(idx, { question_text: e.target.value })} />
              {q.question_type !== "text" && (
                <Input.TextArea rows={2} placeholder="每行一个选项" value={q.options.join("\n")}
                  onChange={e => patchQ(idx, { options: e.target.value.split("\n") })} />
              )}
            </div>
          ))}
          <Button block icon={<PlusOutlined />} onClick={add}>添加题目</Button>
        </Space>
      </Card>
    </div>
  );
}
