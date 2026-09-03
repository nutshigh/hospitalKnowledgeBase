import { useState, useRef, useCallback } from 'react';
import { Upload, Button, Progress, message, Tag, Table, Tooltip } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

const CHUNK_SIZE = 5 * 1024 * 1024; // 5MB,与后端 BATCH_CHUNK_SIZE 对齐
const TERMINAL = ['completed', 'partial_failed', 'cancelled'];

const STATUS_COLOR: Record<string, string> = {
  uploading: 'default', extracting: 'blue', parsing: 'gold',
  interpreting: 'orange', completed: 'green', partial_failed: 'red',
  cancelled: 'default',
};

const UNRETRYABLE_STAGES = new Set(['oversize', 'dispatch_unmatched', 'hospital_not_found']);

const STAGE_LABEL: Record<string, string> = {
  oversize: '文件过大',
  dispatch_unmatched: '命名不合规',
  hospital_not_found: '未匹配到用户/医院',
  parsing: '解析失败',
  interpretation: '解读失败',
};

interface FailingFile {
  id: string;
  file_path: string;
  failed_stage: string | null;
  error_message: string | null;
}

interface BatchProgress {
  id: string;
  filename: string;
  status: string;
  total: number;
  parsed_ok: number;
  interp_ok: number;
  failed: number;
  error_message?: string;
  created_at?: string;
  completed_at?: string;
}

export default function BatchUploadPage() {
  const { api } = useDoctorStore();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'polling'>('idle');
  const [uploaded, setUploaded] = useState(0);
  const [progress, setProgress] = useState<BatchProgress | null>(null);
  const [failing, setFailing] = useState<FailingFile[]>([]);
  const [retrying, setRetrying] = useState(false);
  const batchIdRef = useRef<string | null>(null);

  const poll = useCallback(async (bid: string) => {
    const tick = async () => {
      try {
        const { data } = await api.get(`/reports/batches/${bid}`);
        setProgress(data.batch);
        setFailing(data.failing_files || []);
        if (TERMINAL.includes(data.batch.status)) {
          setPhase('idle'); setBusy(false);
          if (data.batch.status === 'completed') message.success('批量处理完成');
          else if (data.batch.status === 'partial_failed') message.warning('部分文件失败,可在下方查看并重试');
          return;
        }
      } catch { /* 网络抖动,继续 */ }
      timer = window.setTimeout(tick, 5000);
    };
    let timer = window.setTimeout(tick, 3000);
  }, [api]);

  const start = async () => {
    if (!file) return;
    setBusy(true); setPhase('uploading'); setUploaded(0);
    setProgress(null); setFailing([]);
    try {
      // 1. create
      const createForm = new FormData();
      createForm.append('filename', file.name);
      const { data: cd } = await api.post('/reports/batches', createForm);
      const bid = cd.batch_id as string;
      batchIdRef.current = bid;

      // 2. 切片上传(index 0 起)
      const total = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
      for (let i = 0; i < total; i++) {
        const blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
        const form = new FormData();
        form.append('index', String(i));
        form.append('total', String(total));
        form.append('data', blob, `${file.name}.part${i}`);
        await api.post(`/reports/batches/${bid}/chunk`, form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          onUploadProgress: (e) =>
            setUploaded(Math.min(file.size, i * CHUNK_SIZE + (e.loaded || 0))),
        });
      }

      // 3. complete(expected_crc32 留空,靠 expected_size 兜底)
      await api.post(`/reports/batches/${bid}/complete`, {
        expected_total: total, expected_size: file.size,
      });

      // 4. 轮询
      setPhase('polling');
      await poll(bid);
    } catch (err: any) {
      const code = err?.response?.data?.detail;
      message.error(code ? `上传失败: ${code}` : '上传失败,请重试');
      setPhase('idle'); setBusy(false);
    }
  };

  const retryAll = async () => {
    const bid = batchIdRef.current;
    if (!bid) return;
    setRetrying(true);
    try {
      const { data } = await api.post(`/reports/batches/${bid}/retry`, {});
      const rq = data.requeued ?? 0;
      const sk = data.skipped_unretryable ?? 0;
      if (rq > 0) {
        message.success(`已重投 ${rq} 个;跳过 ${sk} 个不可重试`);
        setPhase('polling'); setBusy(true); poll(bid);
      } else {
        message.warning(`无可重试文件;跳过 ${sk} 个不可重试`);
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '重试失败');
    } finally {
      setRetrying(false);
    }
  };

  const pct = file ? Math.round((uploaded / file.size) * 100) : 0;
  const done = progress ? (progress.parsed_ok ?? 0) + (progress.interp_ok ?? 0) + (progress.failed ?? 0) : 0;
  const totalFiles = progress?.total ?? 0;

  const failColumns = [
    { title: '文件', dataIndex: 'file_path', key: 'file_path', width: '40%' },
    {
      title: '失败类型', dataIndex: 'failed_stage', key: 'failed_stage', width: 150,
      render: (s: string | null) => (
        <Tag color={UNRETRYABLE_STAGES.has(s || '') ? 'red' : 'orange'}>
          {s ? (STAGE_LABEL[s] || s) : '失败'}
        </Tag>
      ),
    },
    { title: '原因', dataIndex: 'error_message', key: 'error_message' },
  ];

  return (
    <DoctorLayout>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 24 }}>📦 批量上传分发</h2>

        {/* 命名约定提示卡 */}
        {!busy && (
          <div style={{
            border: '1px solid var(--color-border)', borderRadius: 8,
            padding: '12px 16px', marginBottom: 16, background: 'var(--color-surface)',
            fontSize: 13, color: 'var(--color-text-secondary)',
          }}>
            <div style={{ fontWeight: 600, color: 'var(--color-text)', marginBottom: 6 }}>
              文件命名要求(必须严格遵循)
            </div>
            <div>每份文件名必须形如:<code>张三_011234.pdf</code> 即 <code>&lt;姓名&gt;_&lt;身份证后六位&gt;.ext</code></div>
            <ul style={{ margin: '6px 0 0 20px', padding: 0 }}>
              <li>姓名 + 身份证后六位(<code>5 位数字 + 末位 0-9/X</code>)以半角下划线 <code>_</code> 分隔;姓名不能含下划线</li>
              <li>分发时按 <code>姓名 + 后六位</code> 到外部 HIS 精确匹配定位所属医院,匹配不到将被标记为
                <Tag color="red" style={{ margin: '0 4px' }}>hospital_not_found</Tag>,不解析、不可重试</li>
              <li>命名不合规的文件将被标记为 <Tag color="red" style={{ margin: 0 }}>dispatch_unmatched</Tag>,不解析、不可重试</li>
              <li>扩展名仅支持 pdf / doc / jpg / jpeg / png(不含 docx)</li>
              <li>单文件 ≤ 50MB,整包 ≤ 10GB</li>
            </ul>
          </div>
        )}

        {!busy && (
          <div style={{
            border: file ? '2px solid var(--color-primary)' : '2px dashed var(--color-border)',
            borderRadius: 12, padding: '40px 20px', textAlign: 'center',
            background: 'var(--color-surface)',
          }}>
            <Upload.Dragger
              beforeUpload={(f) => { setFile(f); return false; }}
              showUploadList={false}
              accept=".zip,.tar,.gz,.tgz"
              style={{ background: 'transparent', border: 'none' }}
            >
              <InboxOutlined style={{ fontSize: 48, color: 'var(--color-text-secondary)', marginBottom: 16 }} />
              <p style={{ fontWeight: 600 }}>{file ? file.name : '点击或拖拽上传 zip/tar 包'}</p>
              <p style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                包内文件名须符合上述约定
              </p>
            </Upload.Dragger>
          </div>
        )}

        {file && !busy && (
          <Button type="primary" block size="large" onClick={start} disabled={!file}
            style={{ height: 48, marginTop: 24, background: 'var(--color-primary)', border: 'none' }}>
            开始上传
          </Button>
        )}

        {phase === 'uploading' && (
          <div style={{ marginTop: 24 }}>
            <Progress percent={pct} status="active" />
            <p style={{ textAlign: 'center', color: 'var(--color-text-secondary)', marginTop: 8 }}>
              分片上传中 {pct}%
            </p>
          </div>
        )}

        {phase === 'polling' && progress && (
          <div style={{ marginTop: 24 }}>
            <div style={{ marginBottom: 12 }}>
              <Tag color={STATUS_COLOR[progress.status]}>{progress.status}</Tag>
              <span style={{ marginLeft: 12, color: 'var(--color-text-secondary)' }}>
                {done}/{totalFiles} 文件 · 解析 {progress.parsed_ok} · 解读 {progress.interp_ok} · 失败 {progress.failed}
              </span>
            </div>
            <Progress
              percent={totalFiles ? Math.round((done / totalFiles) * 100) : 0}
              status={progress.status === 'partial_failed' ? 'exception' : 'active'}
            />
            <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 8 }}>
              {['parsing', 'interpreting'].includes(progress.status)
                ? '批量任务在夜间 22:00–08:00 时段处理,白天可能停留在此状态,属正常。'
                : '处理中,每 5s 自动刷新…'}
            </p>
          </div>
        )}

        {failing.length > 0 && (
          <div style={{ marginTop: 24 }}>
            <h3 style={{ fontSize: 14, marginBottom: 8 }}>失败文件 ({failing.length})</h3>
            <Table
              dataSource={failing} columns={failColumns} rowKey="id" size="small"
              pagination={false}
              style={{ background: 'var(--color-surface)', borderRadius: 8 }}
            />
            <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end' }}>
              <Tooltip
                title={failing.some(f => UNRETRYABLE_STAGES.has(f.failed_stage || ''))
                  ? '部分文件(命名/用户医院不匹配/超大)重试无效,请改文件名后重新上传整批'
                  : '重投所有可重试的失败文件'}
              >
                <Button onClick={retryAll} loading={retrying} disabled={retrying}>
                  重试全部可重试失败文件
                </Button>
              </Tooltip>
            </div>
          </div>
        )}
      </div>
    </DoctorLayout>
  );
}