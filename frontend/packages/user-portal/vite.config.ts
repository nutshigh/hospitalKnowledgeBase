import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    // /api 同源代理到本地后端(:8000)。远程浏览器访问时由 vite 转发,
    // 生产构建由 nginx 同源反代(与 doctor-portal 的旧配置一致)。
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
