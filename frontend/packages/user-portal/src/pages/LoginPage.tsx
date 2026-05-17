import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, message } from 'antd';
import { useUserStore } from '../stores/userStore';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const { api, setAuth } = useUserStore();
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
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      background: 'var(--color-surface)', padding: 24,
    }}>
      <div style={{ width: '100%', maxWidth: 360 }}>
        <div style={{ textAlign: 'center', marginBottom: 48 }}>
          <div style={{
            width: 56, height: 56, borderRadius: 16, background: 'var(--color-primary-light)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px',
          }}>
            <span style={{ fontSize: 28 }}>🏥</span>
          </div>
          <h1 style={{ fontSize: 24, marginBottom: 8 }}>体检报告查询</h1>
          <p style={{ color: 'var(--color-text-secondary)', fontSize: 14 }}>登录查看您的体检报告与AI解读</p>
        </div>
        <Input
          size="large" placeholder="用户名" value={username}
          onChange={(e) => setUsername(e.target.value)}
          style={{ marginBottom: 12, borderRadius: 'var(--radius-sm)' }}
          onPressEnter={handleLogin}
        />
        <Input.Password
          size="large" placeholder="密码" value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ marginBottom: 24, borderRadius: 'var(--radius-sm)' }}
          onPressEnter={handleLogin}
        />
        <Button
          type="primary" block size="large" loading={loading} onClick={handleLogin}
          style={{
            height: 48, borderRadius: 'var(--radius-sm)', background: 'var(--color-primary)',
            border: 'none', fontWeight: 600, fontSize: 15,
          }}
        >
          登 录
        </Button>
      </div>
    </div>
  );
}
