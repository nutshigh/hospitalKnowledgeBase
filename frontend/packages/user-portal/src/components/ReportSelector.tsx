import { useState, useEffect } from 'react';
import { Select, message } from 'antd';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';

interface ReportItem {
  id: number;
  name: string | null;
  report_date: string | null;
  check_type: string | null;
}

interface ReportSelectorProps {
  sessionId: number;
}

export default function ReportSelector({ sessionId }: ReportSelectorProps) {
  const { api } = useUserStore();
  const store = useChatStore();
  const [reports, setReports] = useState<ReportItem[]>([]);

  const selectedReportId = store.getSelectedReport(sessionId);

  useEffect(() => {
    api.get('/reports', { params: { page_size: 50 } })
      .then(r => setReports(r.data?.items || []))
      .catch(() => {});
  }, []);

  // 同步当前会话的 report_id 到下拉框
  useEffect(() => {
    if (sessionId) {
      api.get(`/chat/sessions/${sessionId}`)
        .then(r => store.setSelectedReport(sessionId, r.data?.report_id ?? null))
        .catch(() => {});
    }
  }, [sessionId]);

  const handleChange = (val: number | null) => {
    const prev = selectedReportId;
    store.setSelectedReport(sessionId, val ?? null);
    api.patch(`/chat/sessions/${sessionId}`, { report_id: val ?? null })
      .catch(() => {
        // 回滚
        store.setSelectedReport(sessionId, prev);
        message.error('切换报告失败');
      });
  };

  return (
    <Select
      allowClear
      placeholder="选择体检报告"
      style={{ minWidth: 200 }}
      value={selectedReportId}
      onChange={handleChange}
      options={reports.map(r => ({
        value: r.id,
        label: `${r.name || '未命名'} (${r.report_date || '-'})`,
      }))}
    />
  );
}
