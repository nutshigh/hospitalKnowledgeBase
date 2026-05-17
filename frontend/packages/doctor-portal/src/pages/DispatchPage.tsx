import { useEffect, useState } from 'react';
import { Card, Slider, Spin } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

export default function DispatchPage() {
  const { api } = useDoctorStore();
  const [metrics, setMetrics] = useState<any>(null);
  const [config, setConfig] = useState<any>({});

  useEffect(() => {
    api.get('/dispatch/metrics/current').then(r => setMetrics(r.data));
    api.get('/dispatch/config').then(r => setConfig(r.data));
    // eslint-disable-next-line
  }, []);

  if (!metrics) return <DoctorLayout><Spin /></DoctorLayout>;

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>调度管理 & 资源监控</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 24 }}>
        <Card title="CPU" size="small">{metrics.cpu_percent}%</Card>
        <Card title="内存" size="small">{metrics.memory_percent}%</Card>
        <Card title="队列积压" size="small">解析: {metrics.queue_depth_parsing} / 解读: {metrics.queue_depth_interpretation}</Card>
      </div>
      <Card title="并发控制">
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 4 }}>解析 Worker 数</div>
          <Slider min={1} max={8} defaultValue={config.max_parsing_workers || 4}
            onChange={(v) => api.put('/dispatch/config', { max_parsing_workers: v })} />
        </div>
        <div>
          <div style={{ marginBottom: 4 }}>解读 Worker 数</div>
          <Slider min={1} max={4} defaultValue={config.max_interpretation_workers || 2}
            onChange={(v) => api.put('/dispatch/config', { max_interpretation_workers: v })} />
        </div>
      </Card>
    </DoctorLayout>
  );
}
