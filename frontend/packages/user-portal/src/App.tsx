import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AppRouter } from './router';
import './styles/global.css';

export const App = () => (
  <ConfigProvider
    locale={zhCN}
    theme={{
      token: {
        fontFamily: 'var(--font-body)',
        colorPrimary: '#0D9488',
        borderRadius: 8,
      },
    }}
  >
    <BrowserRouter>
      <AppRouter />
    </BrowserRouter>
  </ConfigProvider>
);
