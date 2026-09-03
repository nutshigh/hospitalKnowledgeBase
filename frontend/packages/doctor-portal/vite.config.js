import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3002,
    // 2026-08-31: API 同源代理, 远程浏览器 localhost 访问无需转发 8005
    proxy: {
      "/api": { target: "http://127.0.0.1:8005", changeOrigin: true },
    },
  },
});
