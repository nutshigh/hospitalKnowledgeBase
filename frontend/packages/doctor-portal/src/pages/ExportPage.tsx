import { useState } from 'react';
import { DatePicker, Select, Button, message } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function ExportPage() {
  const { api } = useDoctorStore();
  const [dates, setDates] = useState<[string, string]>(['2024-01-01', '2026-12-31']);
  const [type, setType] = useState('pdf');

  const handleExport = async () => {
    try {
      await api.post('/statistics/export', { hospital_id: '', export_type: type, start_date: dates[0], end_date: dates[1] });
      message.success('导出任务已提交');
    } catch { message.error('导出失败'); }
  };

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>报表导出</h2>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <DatePicker.RangePicker onChange={(_, s) => s && setDates([s[0], s[1]])} />
        <Select value={type} onChange={setType} options={[{ value: 'pdf', label: 'PDF' }, { value: 'word', label: 'Word' }, { value: 'excel', label: 'Excel' }]} />
        <Button type="primary" onClick={handleExport}>导出报表</Button>
      </div>
    </DoctorLayout>
  );
}
