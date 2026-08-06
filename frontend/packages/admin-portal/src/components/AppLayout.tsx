import { Layout, Menu, Button } from 'antd';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import { DashboardOutlined, BarChartOutlined, LogoutOutlined } from '@ant-design/icons';
import { useAdminStore } from '../stores/adminStore';

const { Header, Sider, Content } = Layout;

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { logout } = useAdminStore();
  const currentKey = location.pathname === '/group-analysis' ? 'group-analysis' : 'dashboard';

  const menuItems = [
    { key: 'dashboard', icon: <DashboardOutlined />, label: '已接入医院', onClick: () => navigate('/') },
    { key: 'group-analysis', icon: <BarChartOutlined />, label: '团体分析', onClick: () => navigate('/group-analysis') },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={200} theme="dark">
        <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontWeight: 'bold', fontSize: 18, letterSpacing: 1 }}>体检平台</div>
        <Menu theme="dark" mode="inline" selectedKeys={[currentKey]} items={menuItems} />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', borderBottom: '1px solid #f0f0f0' }}>
          <Button type="text" icon={<LogoutOutlined />} onClick={() => { logout(); navigate('/login'); }}>退出登录</Button>
        </Header>
        <Content style={{ margin: 16, background: '#fff', borderRadius: 8, padding: 24, minHeight: 280 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
