import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Progress, Spin, Table, Tag, message } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
import BatchUploader from '../components/BatchUploader';
import BatchDetailPanel from '../components/BatchDetailPanel';
import { useBatchTracker } from '../hooks/useBatchTracker';
import type { BatchSummary } from '../types/batch';
import { STATUS_COLOR } from '../types/batch';

const PAGE_SIZE = 20;

const fmtTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-';

export default function BatchUploadPage() {
  const { api } = useDoctorStore();
  const [rows, setRows] = useState<BatchSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set());

  const reloadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const { data } = await api.get('/reports/batches', {
        params: { page, page_size: PAGE_SIZE },
      });
      setRows(data.items || []);
      setTotal(data.total ?? 0);
    } catch {
      message.error('历史批次加载失败');
    } finally {
      setHistoryLoading(false);
    }
  }, [api, page]);

  const handleSettled = useCallback((b: BatchSummary) => {
    if (b.status === 'completed') message.success('批量处理完成');
    else if (b.status === 'partial_failed') message.warning('部分文件失败,可在下方查看并重试');
    else if (b.status === 'cancelled') message.info('批次已取消');
    void reloadHistory();
  }, [reloadHistory]);

  const tracker = useBatchTracker(api, handleSettled);
  const { active, loading: activeLoading, error: activeError, wake } = tracker;

  const refresh = useCallback(() => {
    wake();
    void reloadHistory();
  }, [wake, reloadHistory]);

  const handleCreated = useCallback(() => { refresh(); }, [refresh]);
  const handleChanged = useCallback(() => { refresh(); }, [refresh]);

  useEffect(() => { void reloadHistory(); }, [reloadHistory]);

  const cancelBatch = async (bid: string) => {
    try {
      await api.post(`/reports/batches/${bid}/cancel`, {});
      refresh();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '取消失败');
    }
  };

  const toggleCard = (bid: string) => {
    setExpandedCards((prev) => {
      const next = new Set(prev);
      if (next.has(bid)) next.delete(bid); else next.add(bid);
      return next;
    });
  };

  const activeIds = new Set(active.map((b) => b.id));
  const displayRows = rows.filter((r) => !activeIds.has(r.id));
  const nightProcessing = active.some((b) => b.status === 'parsing' || b.status === 'interpreting');

  const columns = [
    { title: '文件名', dataIndex: 'filename', key: 'filename', ellipsis: true,
      render: (v: string) => <span title={v} style={{ display: 'block' }}>{v}</span> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 130,
      render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    { title: '解析', dataIndex: 'parsed_ok', key: 'parsed_ok', width: 70 },
    { title: '解读', dataIndex: 'interp_ok', key: 'interp_ok', width: 70 },
    { title: '失败', dataIndex: 'failed', key: 'failed', width: 70,
      render: (v: number) => (v > 0 ? <span style={{ color: 'var(--color-red)' }}>{v}</span> : v) },
    { title: '总数', dataIndex: 'total', key: 'total', width: 70 },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v?: string | null) => fmtTime(v) },
    { title: '完成时间', dataIndex: 'completed_at', key: 'completed_at', width: 160,
      render: (v?: string | null) => fmtTime(v) },
  ];

  return (
    <DoctorLayout>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 24 }}>📦 批量上传分发</h2>

        <div style={{
          border: '1px solid var(--color-border)', borderRadius: 12,
          padding: 24, background: 'var(--color-surface)', marginBottom: 24,
        }}>
          <h3 style={{ fontSize: 14, marginTop: 0 }}>上传新批次</h3>
          <BatchUploader api={api} onCreated={handleCreated} />
        </div>

        <h3 style={{ fontSize: 14, marginBottom: 8 }}>
          处理中批次 {active.length > 0 && `(${active.length})`}
        </h3>

        {activeLoading && <div style={{ padding: 16 }}><Spin /></div>}
        {activeError && (
          <Alert
            type="error" showIcon style={{ marginBottom: 16 }}
            message="活跃批次加载失败"
            action={<Button size="small" onClick={wake}>重试</Button>}
          />
        )}
        {!activeLoading && !activeError && active.length === 0 && (
          <p style={{ color: 'var(--color-text-secondary)', fontSize: 13, marginBottom: 16 }}>
            当前无处理中批次
          </p>
        )}

        {active.map((b) => {
          const done = (b.interp_ok ?? 0) + (b.failed ?? 0);
          const pct = b.total ? Math.min(100, Math.round((done / b.total) * 100)) : 0;
          return (
            <div key={b.id} style={{
              border: '1px solid var(--color-border)', borderRadius: 8,
              padding: '12px 16px', marginBottom: 12, background: 'var(--color-surface)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {b.filename}
                    </span>
                    <Tag color={STATUS_COLOR[b.status]}>{b.status}</Tag>
                  </div>
                  <Progress
                    percent={pct} status={b.status === 'partial_failed' ? 'exception' : 'active'}
                    size="small" style={{ margin: '8px 0 4px', maxWidth: 520 }}
                  />
                  <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                    {done}/{b.total} 文件 · 解析 {b.parsed_ok} · 解读 {b.interp_ok} · 失败 {b.failed}
                    {' · '}{fmtTime(b.created_at)}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {b.failed > 0 && (
                    <Button size="small" onClick={() => toggleCard(b.id)}>
                      {expandedCards.has(b.id) ? '收起失败' : `失败文件 (${b.failed})`}
                    </Button>
                  )}
                  <Button size="small" danger onClick={() => cancelBatch(b.id)}>取消</Button>
                </div>
              </div>
              {expandedCards.has(b.id) && b.failed > 0 && (
                <div style={{ marginTop: 12 }}>
                  <BatchDetailPanel api={api} batchId={b.id} onChanged={handleChanged} />
                </div>
              )}
            </div>
          );
        })}

        {nightProcessing && active.length > 0 && (
          <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 16 }}>
            批量任务在夜间 22:00–08:00 时段处理,白天可能停留在此状态,属正常。
          </p>
        )}

        <div style={{
          border: '1px solid var(--color-border)', borderRadius: 12,
          padding: 24, background: 'var(--color-surface)', marginTop: 24,
        }}>
          <h3 style={{ fontSize: 14, marginTop: 0 }}>历史批次</h3>
          <Table
            dataSource={displayRows} columns={columns} rowKey="id" loading={historyLoading}
            size="small" style={{ background: 'var(--color-surface)', borderRadius: 8 }}
            pagination={{
              current: page, pageSize: PAGE_SIZE, total, showSizeChanger: false,
              onChange: (p) => setPage(p),
            }}
            expandable={{
              rowExpandable: (r) => (r.failed ?? 0) > 0,
              expandedRowRender: (r) => (
                <BatchDetailPanel api={api} batchId={r.id} onChanged={handleChanged} />
              ),
            }}
            locale={{ emptyText: '暂无批次记录' }}
          />
          {displayRows.length > 0 && (
            <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 8 }}>
              展开含失败文件的批次可查看失败明细并重试。
            </p>
          )}
        </div>
      </div>
    </DoctorLayout>
  );
}
