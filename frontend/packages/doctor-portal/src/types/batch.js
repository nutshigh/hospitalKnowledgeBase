export const ACTIVE_STATUSES = ['extracting', 'parsing', 'interpreting'];
export const TERMINAL_STATUSES = ['completed', 'partial_failed', 'cancelled'];
export const STATUS_COLOR = {
    uploading: 'default', extracting: 'blue', parsing: 'gold',
    interpreting: 'orange', completed: 'green', partial_failed: 'red',
    cancelled: 'default',
};
export const UNRETRYABLE_STAGES = new Set(['oversize', 'dispatch_unmatched', 'hospital_not_found']);
export const STAGE_LABEL = {
    oversize: '文件过大',
    dispatch_unmatched: '命名不合规',
    hospital_not_found: '未匹配到用户/医院',
    parsing: '解析失败',
    interpretation: '解读失败',
};
