import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    // 2026-09-13: API 同源代理 —— 远程浏览器 localhost 访问时, /api 由 vite 转发
    // 到本仓库后端 8000(8005 为 /home/wjyy2 旧 checkout, 无姓名+后六位双锚定)
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
