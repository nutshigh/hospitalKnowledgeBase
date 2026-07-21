import { useAdminStore } from "../stores/adminStore";

const api = () => useAdminStore.getState().api;

export interface OverviewRow {
  key: string;
  label: string;
  total_people: number;
  red_count: number;
  yellow_count: number;
  green_count: number;
  abnormal_rate: number;
  by_gender?: { key: string; count: number }[];
  by_age_group?: { key: string; count: number }[];
  top_abnormal_items?: { item: string; red_count: number }[];
  error?: string;
}

export interface OverviewResponse {
  group_by: string;
  filters: Record<string, any>;
  rows: OverviewRow[];
  totals: Record<string, number>;
}

export interface HighRiskItem {
  hospital_id: string;
  hospital_name: string;
  report_id: number;
  user_id: number;
  name?: string;
  gender?: string;
  age?: number;
  report_date?: string;
  batch_id?: string;
  batch_name?: string;
  overall_level?: string;
  red_count: number;
  yellow_count: number;
  summary_text?: string;
}

export interface HighRiskResponse {
  items: HighRiskItem[];
  total: number;
  page: number;
  page_size: number;
  filters: Record<string, any>;
}

export type GroupBy = "hospital" | "batch" | "age_group" | "gender" | "time_month";

export async function getOverview(params: Record<string, any>): Promise<OverviewResponse> {
  const r = await api().get("/statistics/group/overview", { params });
  return r.data;
}

export async function getHighRisk(params: Record<string, any>): Promise<HighRiskResponse> {
  const r = await api().get("/statistics/group/high-risk", { params });
  return r.data;
}

export async function downloadHighRiskCsv(params: Record<string, any>): Promise<void> {
  const r = await api().get("/statistics/group/high-risk", {
    params: { ...params, format: "csv" },
    responseType: "blob",
  });
  const url = URL.createObjectURL(r.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = "high-risk.csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
