/// <reference types="vite/client" />
import axios, { AxiosInstance } from "axios";

// 2026-08-31: API 走同源相对路径 /api/v1 —— 由 vite dev server 代理到 127.0.0.1:8005
// (vite.config.ts server.proxy), 生产由 nginx 同源反代。
// 远程浏览器无论用 localhost 还是 IP 访问 3011, /api 请求都发给 3011 自身,
// 不依赖用户转发 8005 端口, 也不受 localhost/IPv6 解析影响。
const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export const createApiClient = (getToken: () => string | null): AxiosInstance => {
  const client = axios.create({ baseURL: BASE_URL });

  client.interceptors.request.use((config) => {
    const token = getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  client.interceptors.response.use(
    (response) => response,
    (error) => {
      if (error.response?.status === 401) {
        localStorage.removeItem("token");
        window.location.href = "/login";
      }
      return Promise.reject(error);
    }
  );

  return client;
};
