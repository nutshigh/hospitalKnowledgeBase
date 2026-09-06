export const ACTIVE_STATUSES = ['extracting', 'parsing', 'interpreting'] as const;
export const TERMINAL_STATUSES = ['completed', 'partial_failed', 'cancelled'] as const;

export interface BatchSummary {
  id: string;
  filename: string;
  status: string;
  total: number;
  parsed_ok: number;
  interp_ok: number;
  failed: number;
  created_at?: string | null;
  completed_at?: string | null;
}

export interface FailingFile {
  id: string;
  file_path: string;
  failed_stage: string | null;
  error_message: string | null;
}

export interface BatchDetail {
  batch: BatchSummary;
  failing_files: FailingFile[];
}

export const STATUS_COLOR: Record<string, string> = {
  uploading: 'default', extracting: 'blue', parsing: 'gold',
  interpreting: 'orange', completed: 'green', partial_failed: 'red',
  cancelled: 'default',
};

export const UNRETRYABLE_STAGES = new Set(['oversize', 'dispatch_unmatched', 'hospital_not_found']);

export const STAGE_LABEL: Record<string, string> = {
  oversize: '文件过大',
  dispatch_unmatched: '命名不合规',
  hospital_not_found: '未匹配到用户/医院',
  parsing: '解析失败',
  interpretation: '解读失败',
};
