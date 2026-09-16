import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3003,
    // 2026-09-14: 绑定 0.0.0.0, 支持远程浏览器用服务器 IP 访问(原默认仅 127.0.0.1)
    host: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
