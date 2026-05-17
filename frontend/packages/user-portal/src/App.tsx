import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { AppRouter } from "./router";

export const App = () => (
  <ConfigProvider locale={zhCN}>
    <BrowserRouter>
      <AppRouter />
    </BrowserRouter>
  </ConfigProvider>
);
