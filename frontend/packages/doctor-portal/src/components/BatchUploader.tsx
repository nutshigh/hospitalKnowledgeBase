import { useState } from 'react';
import { Upload, Button, Progress, message } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import type { ApiClient } from '@hospital/shared';

const CHUNK_SIZE = 5 * 1024 * 1024;

interface Props {
  api: ApiClient;
  onCreated: (batchId: string) => void;
}

export default function BatchUploader({ api, onCreated }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState(0);

  const start = async () => {
    if (!file || uploading) return;
    setUploading(true); setUploaded(0);
    const total = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
    try {
      const createForm = new FormData();
      createForm.append('filename', file.name);
      const { data: cd } = await api.post('/reports/batches', createForm);
      const bid = cd.batch_id as string;

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

      await api.post(`/reports/batches/${bid}/complete`, {
        expected_total: total, expected_size: file.size,
      });

      setFile(null); setUploaded(0);
      onCreated(bid);
    } catch (err: any) {
      const code = err?.response?.data?.detail;
      message.error(code ? `上传失败: ${code}` : '上传失败,请重试');
    } finally {
      setUploading(false);
    }
  };

  const pct = file && file.size ? Math.round((uploaded / file.size) * 100) : 0;

  return (
    <div>
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
            失败类型 <code>hospital_not_found</code>,不解析、不可重试</li>
          <li>命名不合规的文件将被标记为 <code>dispatch_unmatched</code>,不解析、不可重试</li>
          <li>扩展名仅支持 pdf / doc / jpg / jpeg / png(不含 docx)</li>
          <li>单文件 ≤ 50MB,整包 ≤ 10GB</li>
        </ul>
      </div>

      <Upload.Dragger
        beforeUpload={(f) => { setFile(f); return false; }}
        showUploadList={false}
        accept=".zip,.tar,.gz,.tgz"
        disabled={uploading}
        style={{ background: 'var(--color-surface)', border: file ? '2px solid var(--color-primary)' : undefined }}
      >
        <InboxOutlined style={{ fontSize: 48, color: 'var(--color-text-secondary)', marginBottom: 16 }} />
        <p style={{ fontWeight: 600 }}>{file ? file.name : '点击或拖拽上传 zip/tar 包'}</p>
        <p style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
          包内文件名须符合上述约定
        </p>
      </Upload.Dragger>

      {file && !uploading && (
        <Button type="primary" block size="large" onClick={start}
          style={{ height: 48, marginTop: 16, background: 'var(--color-primary)', border: 'none' }}>
          开始上传
        </Button>
      )}

      {uploading && (
        <div style={{ marginTop: 16 }}>
          <Progress percent={pct} status="active" />
          <p style={{ textAlign: 'center', color: 'var(--color-text-secondary)', marginTop: 8 }}>
            分片上传中 {pct}%
          </p>
        </div>
      )}
    </div>
  );
}
