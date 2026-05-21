import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Upload, Button, message, Progress } from 'antd';
import { CameraOutlined, FileOutlined, ArrowLeftOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';

export default function UploadPage() {
  const { api } = useUserStore();
  const nav = useNavigate();
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const fileRef = useRef<File | null>(null);

  const handleUpload = async () => {
    if (!fileRef.current) return;
    setUploading(true);
    const form = new FormData();
    form.append('file', fileRef.current);
    try {
      const res = await api.post('/reports/upload', form, {
        onUploadProgress: (e: any) => setProgress(Math.round((e.loaded / e.total) * 100)),
      });
      message.success('上传成功，报告解析中，请稍后刷新查看');
      nav('/');
    } catch {
      message.error('上传失败');
    } finally {
      setUploading(false);
    }
  };

  return (
    <Layout title="上传报告">
      <button onClick={() => nav(-1)} style={{
        border: 'none', background: 'none', fontSize: 14, color: 'var(--color-primary)',
        cursor: 'pointer', marginBottom: 24, display: 'flex', alignItems: 'center', gap: 4,
      }}>
        <ArrowLeftOutlined /> 返回
      </button>
      <div style={{
        border: '2px dashed var(--color-border)', borderRadius: 'var(--radius-lg)',
        padding: 48, textAlign: 'center', marginBottom: 24,
        background: fileRef.current ? 'var(--color-primary-light)' : 'var(--color-bg)',
        transition: '0.2s',
      }}>
        <Upload.Dragger
          beforeUpload={(f) => { fileRef.current = f; return false; }}
          showUploadList={false}
          accept=".pdf,.docx,.doc,.jpg,.jpeg,.png"
          style={{ background: 'transparent', border: 'none' }}
        >
          <div style={{ fontSize: 40, marginBottom: 16 }}>
            {fileRef.current ? <FileOutlined style={{ color: 'var(--color-primary)' }} /> : <CameraOutlined />}
          </div>
          <p style={{ fontWeight: 600, marginBottom: 4 }}>
            {fileRef.current ? fileRef.current.name : '点击或拖拽上传体检报告'}
          </p>
          <p style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
            支持 PDF、Word、JPG、PNG 格式
          </p>
        </Upload.Dragger>
      </div>
      {uploading && <Progress percent={progress} style={{ marginBottom: 16 }} strokeColor="var(--color-primary)" />}
      <Button
        type="primary" block size="large" onClick={handleUpload}
        disabled={!fileRef.current} loading={uploading}
        style={{ height: 48, borderRadius: 'var(--radius-sm)', background: 'var(--color-primary)', border: 'none', fontWeight: 600 }}
      >
        开始解析
      </Button>
    </Layout>
  );
}
