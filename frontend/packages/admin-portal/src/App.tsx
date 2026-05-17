import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AppRouter } from './router';

export const App = () => (
  <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#0D9488' } }}>
    <BrowserRouter><AppRouter /></BrowserRouter>
  </ConfigProvider>
);
