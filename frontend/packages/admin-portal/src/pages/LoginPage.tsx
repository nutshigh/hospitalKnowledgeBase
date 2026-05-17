import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, message } from 'antd';
import { useAdminStore } from '../stores/adminStore';

export default function LoginPage() {
  const [u, setU] = useState(''); const [p, setP] = useState('');
  const [loading, setLoading] = useState(false);
  const { api, setAuth } = useAdminStore(); const nav = useNavigate();

  const login = async () => {
    setLoading(true);
    try {
      const r = await api.post('/auth/login', { username: u, password: p });
      setAuth(r.data.access_token); message.success('登录成功'); nav('/');
    } catch { message.error('登录失败'); }
    finally { setLoading(false); }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F5F5F4' }}>
      <div style={{ width: 360, background: '#fff', borderRadius: 12, padding: 40, boxShadow: '0 8px 32px rgba(0,0,0,0.08)' }}>
        <h2 style={{ textAlign: 'center', marginBottom: 24 }}>平台管理后台</h2>
        <Input size="large" placeholder="管理员账号" value={u} onChange={e => setU(e.target.value)} style={{ marginBottom: 12 }} onPressEnter={login} />
        <Input.Password size="large" placeholder="密码" value={p} onChange={e => setP(e.target.value)} style={{ marginBottom: 20 }} onPressEnter={login} />
        <Button type="primary" block size="large" loading={loading} onClick={login}>登 录</Button>
      </div>
    </div>
  );
}
