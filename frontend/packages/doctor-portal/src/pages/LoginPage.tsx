import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, message } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const { api, setAuth } = useDoctorStore();
  const nav = useNavigate();

  const handleLogin = async () => {
    setLoading(true);
    try {
      const res = await api.post('/auth/login', { username, password });
      setAuth(res.data.access_token, res.data.user_id, res.data.role, res.data.hospital_id || '');
      message.success('登录成功');
      nav('/');
    } catch {
      message.error('用户名或密码错误');
    } finally { setLoading(false); }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--color-bg)' }}>
      <div style={{ width: 360, background: 'var(--color-surface)', borderRadius: 'var(--radius-lg)', padding: 40, boxShadow: 'var(--shadow-lg)' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <span style={{ fontSize: 32 }}>🏥</span>
          <h1 style={{ fontSize: 20, marginTop: 12 }}>医生工作台</h1>
          <p style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>AI 体检报告解读系统</p>
        </div>
        <Input size="large" placeholder="用户名" value={username} onChange={e => setUsername(e.target.value)} style={{ marginBottom: 12 }} onPressEnter={handleLogin} />
        <Input.Password size="large" placeholder="密码" value={password} onChange={e => setPassword(e.target.value)} style={{ marginBottom: 20 }} onPressEnter={handleLogin} />
        <Button type="primary" block size="large" loading={loading} onClick={handleLogin}
          style={{ height: 44, background: 'var(--color-primary)', border: 'none', fontWeight: 600 }}>登 录</Button>
      </div>
    </div>
  );
}
