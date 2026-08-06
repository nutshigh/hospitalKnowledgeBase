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

export interface TenantItem {
  hospital_id: string;
  hospital_name: string;
  is_active: number;
}

export interface TenantListResponse {
  items: TenantItem[];
  total: number;
}

export async function listTenants(activeOnly = true): Promise<TenantItem[]> {
  const r = await api().get("/tenants", { params: { active_only: activeOnly } });
  return r.data.items;
}

// Backend _filters uses Query(None): Optional[str] + parse_csv_query, which
// expects a single comma-joined string per param. axios default serializes
// arrays as repeated query keys (`?hospital_ids[]=H001&hospital_ids[]=H002`),
// which FastAPI silently drops -> filter is ignored. Flatten arrays here so
// the backend sees `?hospital_ids=H001,H002` and parse_csv_query can split.
function _flattenFilters(params: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) {
      const joined = v.filter(Boolean).join(",");
      if (joined) out[k] = joined;
    } else if (v !== undefined && v !== null && v !== "") {
      out[k] = v;
    }
  }
  return out;
}

export async function getOverview(params: Record<string, any>): Promise<OverviewResponse> {
  const r = await api().get("/statistics/group/overview", { params: _flattenFilters(params) });
  return r.data;
}

export async function getHighRisk(params: Record<string, any>): Promise<HighRiskResponse> {
  const r = await api().get("/statistics/group/high-risk", { params: _flattenFilters(params) });
  return r.data;
}

export async function downloadHighRiskCsv(params: Record<string, any>): Promise<void> {
  const r = await api().get("/statistics/group/high-risk", {
    params: _flattenFilters({ ...params, format: "csv" }),
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
