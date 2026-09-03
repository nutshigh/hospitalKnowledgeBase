import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, message, Spin, Table, Tag, Tooltip } from 'antd';
import type { ApiClient } from '@hospital/shared';
import type { BatchDetail, FailingFile } from '../types/batch';
import { STAGE_LABEL, UNRETRYABLE_STAGES } from '../types/batch';

const failColumns = [
  { title: '文件', dataIndex: 'file_path', key: 'file_path', width: '45%' },
  {
    title: '失败类型', dataIndex: 'failed_stage', key: 'failed_stage', width: 160,
    render: (s: string | null) => (
      <Tag color={UNRETRYABLE_STAGES.has(s || '') ? 'red' : 'orange'}>
        {s ? (STAGE_LABEL[s] || s) : '失败'}
      </Tag>
    ),
  },
  { title: '原因', dataIndex: 'error_message', key: 'error_message' },
];

interface Props {
  api: ApiClient;
  batchId: string;
  onChanged: () => void;
}

export default function BatchDetailPanel({ api, batchId, onChanged }: Props) {
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const { data } = await api.get(`/reports/batches/${batchId}`);
      setDetail(data);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [api, batchId]);

  useEffect(() => { void load(); }, [load]);

  const retry = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/reports/batches/${batchId}/retry`, {});
      const rq = data.requeued ?? 0;
      const sk = data.skipped_unretryable ?? 0;
      if (rq > 0) message.success(`已重投 ${rq} 个;跳过 ${sk} 个不可重试`);
      else message.warning(`无可重试文件;跳过 ${sk} 个不可重试`);
      onChanged();
      void load();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '重试失败');
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Spin size="small" style={{ margin: 8 }} />;

  if (loadError || !detail) {
    return (
      <Alert
        type="error" showIcon style={{ margin: '8px 0' }}
        message="批次详情加载失败"
        action={<Button size="small" onClick={() => void load()}>重试</Button>}
      />
    );
  }

  const failing: FailingFile[] = detail.failing_files || [];
  const retryable = failing.some((f) => !UNRETRYABLE_STAGES.has(f.failed_stage || ''));

  return (
    <div style={{ padding: '8px 0' }}>
      <Table
        dataSource={failing} columns={failColumns} rowKey="id" size="small"
        pagination={false} locale={{ emptyText: '无失败文件' }}
        style={{ background: 'var(--color-surface)', borderRadius: 8 }}
      />
      {failing.length > 0 && (
        <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
          <Tooltip
            title={retryable ? '重投所有可重试的失败文件'
              : '命名/用户医院不匹配/超大 等不可重试,请改文件名后重新上传整批'}
          >
            <Button onClick={retry} loading={busy} disabled={busy} type="primary" size="small">
              重试全部可重试
            </Button>
          </Tooltip>
        </div>
      )}
    </div>
  );
}
