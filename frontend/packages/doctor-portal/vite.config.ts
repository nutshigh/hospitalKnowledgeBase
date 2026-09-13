import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3002,
    // 2026-09-13: API 同源代理 —— 与 user-portal 一致转发到本仓库后端 8000
    // (8005 为 /home/wjyy2 旧 checkout, 已停用)
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
