import { useAdminStore } from "../stores/adminStore";

const api = () => useAdminStore.getState().api;

export interface TemplateQuestion {
  id?: number | null;
  question_type: "single" | "multiple" | "text";
  question_text: string;
  options: string[];
  is_required: boolean;
  sort_order: number;
  is_active: boolean;
}

export interface FollowupTemplate {
  id: number | null;
  name: string;
  description: string | null;
  questions: TemplateQuestion[];
}

export async function getTemplate(): Promise<FollowupTemplate> {
  const r = await api().get("/followup/template");
  return r.data;
}

export async function saveTemplate(t: FollowupTemplate): Promise<FollowupTemplate> {
  const r = await api().put("/followup/template", t);
  return r.data;
}
