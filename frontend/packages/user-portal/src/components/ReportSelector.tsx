import { useState, useEffect } from 'react';
import { Select } from 'antd';
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

  return (
    <Select
      allowClear
      placeholder="选择体检报告"
      style={{ minWidth: 200 }}
      value={selectedReportId}
      onChange={(val) => store.setSelectedReport(sessionId, val ?? null)}
      options={reports.map(r => ({
        value: r.id,
        label: `${r.name || '未命名'} (${r.report_date || '-'})`,
      }))}
    />
  );
}
