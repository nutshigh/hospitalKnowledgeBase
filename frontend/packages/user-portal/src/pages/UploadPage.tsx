import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Upload, Button, message } from 'antd';
import { InboxOutlined, LoadingOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';

export default function UploadPage() {
  const { api } = useUserStore();
  const nav = useNavigate();
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState<File | null>(null);

  const doUpload = async () => {
    if (!file) return;
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await api.post('/reports/upload', form);
      if (res.data?.task_id) {
        message.success('提交成功，报告分析中');
        nav('/');
      } else {
        message.error('提交失败，请重试');
      }
    } catch (err: any) {
      if (err?.response?.status === 401) {
        message.error('登录已过期，请重新登录');
      } else {
        message.error('上传失败，请重试');
      }
    } finally {
      setUploading(false);
    }
  };

  return (
    <Layout title="上传报告">
      <div style={{
        border: file ? '2px solid #0D9488' : '2px dashed #d9d9d9',
        borderRadius: 12, padding: '40px 20px', textAlign: 'center', marginBottom: 24,
        background: file ? '#f0fdfa' : '#fafafa', transition: '0.2s',
      }}>
        <Upload.Dragger
          beforeUpload={(f) => { setFile(f); return false; }}
          showUploadList={false}
          accept=".pdf,.docx,.doc,.jpg,.jpeg,.png"
          disabled={uploading}
          style={{ background: 'transparent', border: 'none' }}
        >
          <InboxOutlined style={{ fontSize: 48, color: file ? '#0D9488' : '#bfbfbf', marginBottom: 16 }} />
          <p style={{ fontWeight: 600, fontSize: 15, marginBottom: 4 }}>
            {file ? file.name : '点击或拖拽上传体检报告'}
          </p>
          <p style={{ fontSize: 12, color: '#999' }}>PDF / Word / JPG / PNG，最大 20MB</p>
        </Upload.Dragger>
      </div>

      <Button
        type="primary" block size="large" onClick={doUpload}
        disabled={!file || uploading} loading={uploading}
        style={{ height: 48, borderRadius: 8, background: '#0D9488', border: 'none', fontWeight: 600, fontSize: 16 }}
      >
        {uploading ? '上传中...' : '提交解析'}
      </Button>

      <div style={{ textAlign: 'center', marginTop: 16 }}>
        <button onClick={() => nav(-1)} style={{
          border: 'none', background: 'none', color: '#0D9488', cursor: 'pointer', fontSize: 14,
        }}>← 返回首页</button>
      </div>
    </Layout>
  );
}
