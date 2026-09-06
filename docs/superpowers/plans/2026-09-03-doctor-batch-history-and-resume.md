# 医生端批量上传:历史列表 + 刷新自动恢复 + 多批次并行 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复医生端 `/batch` 页刷新后丢失批次处理进度的问题,并支持查看全部历史批次、自动恢复对未终态批次的轮询、多批次并行处理。

**Architecture:** 后端仅增强 `GET /reports/batches`(status 多值 OR + list 项补 `completed_at`),数据层零改动。前端把 `/batch` 单页重构为「常驻上传卡片 + 处理中卡片区(由 `useBatchTracker` 轮询) + 历史批次表(可展开失败明细/重试/取消)」。

**Tech Stack:** FastAPI + SQLAlchemy(pytest),React 18 + antd 5 + zustand + axios(tsc/vite build)。

**Spec:** `docs/superpowers/specs/2026-09-03-doctor-batch-history-and-resume-design.md`

## Global Constraints

- 后端只改 `backend/app/modules/report/batch_router.py::list_batches`;不得动 models / DDL / 跨院分发逻辑
- 旧调用方式 `?status=x` 单值必须继续工作(list 参数需向后兼容)
- 后端测试跑法:`cd backend && .venv/bin/python -m pytest tests/<file> -q -x`(venv 见 AGENTS.md,勿用系统 python)
- 前端无单测基建 → 验证靠 `cd frontend/packages/doctor-portal && npm run build`(tsc) + 手工清单
- 现状页面已渲染的文案/状态色/不可重试阶段标签须原样保留(见 Task 2 types 常量)
- 活跃批次定义 = `extracting|parsing|interpreting`;终态 = `completed|partial_failed|cancelled`
- 不新增前端路由/菜单项;全部并入现有 `/batch` 页
- 遵循「不加注释」与仓库现有行内样式模式;antd v5(Table `expandable`、`Alert action`、`Progress` 均已使用)

---

### Task 1: 后端 `list_batches` 支持多 status OR + `completed_at`

**Files:**
- Modify: `backend/app/modules/report/batch_router.py:1`(import)+`:92-112`(list_batches)
- Test: `backend/tests/test_batch_router.py`(append new test)

**Interfaces:**
- Consumes: `BatchImport`(现有 model)
- Produces: `GET /reports/batches?status=extracting&status=parsing&...` 返回非终态批次列表(供 Task 3 活跃轮询);list 项含 `completed_at`;`?status=x` 单值行为不变

- [ ] **Step 1: 补 import `List`**

在 `backend/app/modules/report/batch_router.py` 顶部把 `from typing import Optional` 改为:

```python
from typing import List, Optional
```

- [ ] **Step 2: 改 `list_batches` 签名与过滤、list 项补 `completed_at`**

把 `batch_router.py` 92-112 行的函数整体替换为:

```python
@router.get("/batches")
def list_batches(page: int = Query(1, ge=1),
                 page_size: int = Query(20, ge=1, le=100),
                 status: Optional[List[str]] = Query(None),
                 db: Session = Depends(_db),
                 user: CurrentUser = Depends(get_current_user)):
    q = db.query(BatchImport).filter_by(hospital_id=user.hospital_id)
    if status:
        q = q.filter(BatchImport.status.in_(status))
    total = q.count()
    items = (q.order_by(BatchImport.created_at.desc())
              .offset((page - 1) * page_size).limit(page_size).all())
    return {
        "items": [{
            "id": b.id, "filename": b.filename, "status": b.status,
            "total": b.total, "parsed_ok": b.parsed_ok, "interp_ok": b.interp_ok,
            "failed": b.failed,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "completed_at": b.completed_at.isoformat() if b.completed_at else None,
        } for b in items],
        "total": total, "page": page, "page_size": page_size,
    }
```

- [ ] **Step 3: 追加后端测试**

在 `backend/tests/test_batch_router.py` 末尾(280 行后)追加:

```python
def test_T11_list_batches_status_or_and_completed_at(env):
    """多 status OR 过滤 + list 项含 completed_at(向后兼容单值 status)。"""
    from datetime import datetime, timezone
    s = env["Session"]()
    rows = [
        ("so0", "completed"),
        ("so1", "partial_failed"),
        ("so2", "parsing"),
        ("so3", "interpreting"),
        ("so4", "cancelled"),
    ]
    for bid, st in rows:
        s.add(BatchImport(id=bid, hospital_id="H001", user_id="1",
                          filename=f"{st}.zip", archive_path="/x", status=st,
                          completed_at=datetime.now(timezone.utc) if st == "completed" else None))
    s.commit(); s.close()

    # 多 status OR:只回 parsing + interpreting
    r = env["client"].get(
        "/api/v1/reports/batches",
        params=[("status", "parsing"), ("status", "interpreting")],
    )
    assert r.status_code == 200
    body = r.json()
    assert {x["status"] for x in body["items"]} == {"parsing", "interpreting"}
    assert body["total"] == 2

    # 单值 status 行为不变
    r1 = env["client"].get("/api/v1/reports/batches?status=completed")
    assert r1.status_code == 200
    items1 = r1.json()["items"]
    assert len(items1) == 1 and items1[0]["status"] == "completed"
    assert "completed_at" in items1[0]
    assert items1[0]["completed_at"] is not None

    # 未终态行 completed_at 为 null
    r2 = env["client"].get("/api/v1/reports/batches?status=parsing")
    assert r2.status_code == 200
    assert r2.json()["items"][0]["completed_at"] is None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_batch_router.py -q -x`
Expected: 全部 PASS(含既有用例,证明向后兼容)

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/report/batch_router.py backend/tests/test_batch_router.py
git commit -m "feat: 批量批次列表支持多 status OR 过滤并补 completed_at"
```

---

### Task 2: 前端基建 —— shared `ApiClient` 类型导出 + `types/batch.ts`

**Files:**
- Modify: `frontend/packages/shared/src/api/client.ts`
- Modify: `frontend/packages/shared/src/index.ts`
- Create: `frontend/packages/doctor-portal/src/types/batch.ts`

**Interfaces:**
- Produces: `import type { ApiClient } from '@hospital/shared'`(axios 实例类型);`ACTIVE_STATUSES`/`TERMINAL_STATUSES`/`BatchSummary`/`FailingFile`/`BatchDetail`/`STATUS_COLOR`/`UNRETRYABLE_STAGES`/`STAGE_LABEL`(Task 3-6 共用)
- Note: 类型与常量从旧 `BatchUploadPage.tsx` 顶部搬来,内容原样保留,供后续页面引用

- [ ] **Step 1: shared 导出 `ApiClient` 类型**

`frontend/packages/shared/src/api/client.ts` 末尾追加:

```ts
export type ApiClient = AxiosInstance;
```

`frontend/packages/shared/src/index.ts` 改为:

```ts
export { createApiClient } from "./api/client";
export type { ApiClient } from "./api/client";
export * from "./components/InterpretationReport";
```

- [ ] **Step 2: 新建 `types/batch.ts`**

Create `frontend/packages/doctor-portal/src/types/batch.ts`:

```ts
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
```

- [ ] **Step 3: 编译验证**

Run: `cd frontend/packages/doctor-portal && npm run build`
Expected: tsc + vite build 成功退出

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/shared/src/api/client.ts frontend/packages/shared/src/index.ts frontend/packages/doctor-portal/src/types/batch.ts
git commit -m "feat: 医生端 batch 类型与常量抽取,shared 导出 ApiClient 类型"
```

---

### Task 3: `hooks/useBatchTracker.ts` —— 活跃批次发现 + 轮询 + 自动恢复

**Files:**
- Create: `frontend/packages/doctor-portal/src/hooks/useBatchTracker.ts`

**Interfaces:**
- Consumes: `api: ApiClient`(Task 2);`BatchSummary`/`ACTIVE_STATUSES`(Task 2)
- Produces: `useBatchTracker(api, onSettled?) => { active: BatchSummary[]; loading: boolean; error: boolean; wake: () => void }`
  - `active` 按 created_at desc(后端已排序),含 mount 时自动发现的未终态批次
  - 有活跃批次时每 5s 轮询一次(单请求);返回为空则停表;`wake()` 立即刷新并重排计时
  - 批次脱离活跃集(转终态/消失)→ 追加一次 `GET /reports/batches/{id}` 拿终态后调 `onSettled(batch)`
  - 请求失败:标记 `error`;若仍保有活跃批次则 5s 后重试,否则停表(页面以 `error` 显示重试入口)
  - 卸载清理定时器

- [ ] **Step 1: 新建 hook**

Create `frontend/packages/doctor-portal/src/hooks/useBatchTracker.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ApiClient } from '@hospital/shared';
import { ACTIVE_STATUSES } from '../types/batch';
import type { BatchSummary } from '../types/batch';

const POLL_MS = 5000;
export const ACTIVE_QUERY =
  `/reports/batches?${ACTIVE_STATUSES.map((s) => `status=${s}`).join('&')}&page_size=100`;

export function useBatchTracker(api: ApiClient, onSettled?: (b: BatchSummary) => void) {
  const [active, setActive] = useState<BatchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const timerRef = useRef<number | null>(null);
  const activeRef = useRef<BatchSummary[]>([]);
  const onSettledRef = useRef(onSettled);
  onSettledRef.current = onSettled;

  const stop = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const schedule = useCallback((fn: () => void) => {
    stop();
    timerRef.current = window.setTimeout(fn, POLL_MS);
  }, [stop]);

  const fetchActive = useCallback(async () => {
    try {
      const { data } = await api.get(ACTIVE_QUERY);
      const items: BatchSummary[] = data.items || [];
      const prev = activeRef.current;
      const nextIds = new Set(items.map((b) => b.id));
      const settled = prev.filter((b) => !nextIds.has(b.id));
      activeRef.current = items;
      setActive(items);
      setError(false);
      setLoading(false);
      if (settled.length > 0) {
        void (async () => {
          const finals = await Promise.all(settled.map(async (b) => {
            try {
              const { data: d } = await api.get(`/reports/batches/${b.id}`);
              return (d.batch as BatchSummary) || b;
            } catch {
              return b;
            }
          }));
          for (const f of finals) onSettledRef.current?.(f);
        })();
      }
      if (items.length > 0) schedule(() => { void fetchActive(); });
    } catch {
      setError(true);
      setLoading(false);
      if (activeRef.current.length > 0) schedule(() => { void fetchActive(); });
    }
  }, [api, schedule]);

  const wake = useCallback(() => {
    schedule(() => { void fetchActive(); });
  }, [fetchActive, schedule]);

  useEffect(() => {
    void fetchActive();
    return stop;
  }, [fetchActive, stop]);

  return { active, loading, error, wake };
}
```

- [ ] **Step 2: 编译验证**

Run: `cd frontend/packages/doctor-portal && npm run build`
Expected: tsc + vite build 成功退出

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/doctor-portal/src/hooks/useBatchTracker.ts
git commit -m "feat: useBatchTracker 活跃批次轮询与刷新自动恢复"
```

---

### Task 4: `components/BatchUploader.tsx` —— 常驻上传卡片

**Files:**
- Create: `frontend/packages/doctor-portal/src/components/BatchUploader.tsx`

**Interfaces:**
- Consumes: `api: ApiClient`
- Produces: `BatchUploader({ api, onCreated }: { api: ApiClient; onCreated: (batchId: string) => void })`
  - 分片上传逻辑从旧页原样搬入,上传中按钮/拖拽禁用,页内单飞
  - 完成后 `setFile(null)` 并回调 `onCreated(bid)`,由父页 `wake()` 立刻拉入活跃区

- [ ] **Step 1: 新建组件**

Create `frontend/packages/doctor-portal/src/components/BatchUploader.tsx`:

```tsx
import { useState } from 'react';
import { Upload, Button, Progress, message } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import type { ApiClient } from '@hospital/shared';

const CHUNK_SIZE = 5 * 1024 * 1024;

interface Props {
  api: ApiClient;
  onCreated: (batchId: string) => void;
}

export default function BatchUploader({ api, onCreated }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState(0);

  const start = async () => {
    if (!file || uploading) return;
    setUploading(true); setUploaded(0);
    const total = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
    try {
      const createForm = new FormData();
      createForm.append('filename', file.name);
      const { data: cd } = await api.post('/reports/batches', createForm);
      const bid = cd.batch_id as string;

      for (let i = 0; i < total; i++) {
        const blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
        const form = new FormData();
        form.append('index', String(i));
        form.append('total', String(total));
        form.append('data', blob, `${file.name}.part${i}`);
        await api.post(`/reports/batches/${bid}/chunk`, form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          onUploadProgress: (e) =>
            setUploaded(Math.min(file.size, i * CHUNK_SIZE + (e.loaded || 0))),
        });
      }

      await api.post(`/reports/batches/${bid}/complete`, {
        expected_total: total, expected_size: file.size,
      });

      setFile(null); setUploaded(0);
      onCreated(bid);
    } catch (err: any) {
      const code = err?.response?.data?.detail;
      message.error(code ? `上传失败: ${code}` : '上传失败,请重试');
    } finally {
      setUploading(false);
    }
  };

  const pct = file && file.size ? Math.round((uploaded / file.size) * 100) : 0;

  return (
    <div>
      <div style={{
        border: '1px solid var(--color-border)', borderRadius: 8,
        padding: '12px 16px', marginBottom: 16, background: 'var(--color-surface)',
        fontSize: 13, color: 'var(--color-text-secondary)',
      }}>
        <div style={{ fontWeight: 600, color: 'var(--color-text)', marginBottom: 6 }}>
          文件命名要求(必须严格遵循)
        </div>
        <div>每份文件名必须形如:<code>张三_011234.pdf</code> 即 <code>&lt;姓名&gt;_&lt;身份证后六位&gt;.ext</code></div>
        <ul style={{ margin: '6px 0 0 20px', padding: 0 }}>
          <li>姓名 + 身份证后六位(<code>5 位数字 + 末位 0-9/X</code>)以半角下划线 <code>_</code> 分隔;姓名不能含下划线</li>
          <li>分发时按 <code>姓名 + 后六位</code> 到外部 HIS 精确匹配定位所属医院,匹配不到将被标记为
            失败类型 <code>hospital_not_found</code>,不解析、不可重试</li>
          <li>命名不合规的文件将被标记为 <code>dispatch_unmatched</code>,不解析、不可重试</li>
          <li>扩展名仅支持 pdf / doc / jpg / jpeg / png(不含 docx)</li>
          <li>单文件 ≤ 50MB,整包 ≤ 10GB</li>
        </ul>
      </div>

      <Upload.Dragger
        beforeUpload={(f) => { setFile(f); return false; }}
        showUploadList={false}
        accept=".zip,.tar,.gz,.tgz"
        disabled={uploading}
        style={{ background: 'var(--color-surface)', border: file ? '2px solid var(--color-primary)' : undefined }}
      >
        <InboxOutlined style={{ fontSize: 48, color: 'var(--color-text-secondary)', marginBottom: 16 }} />
        <p style={{ fontWeight: 600 }}>{file ? file.name : '点击或拖拽上传 zip/tar 包'}</p>
        <p style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
          包内文件名须符合上述约定
        </p>
      </Upload.Dragger>

      {file && !uploading && (
        <Button type="primary" block size="large" onClick={start}
          style={{ height: 48, marginTop: 16, background: 'var(--color-primary)', border: 'none' }}>
          开始上传
        </Button>
      )}

      {uploading && (
        <div style={{ marginTop: 16 }}>
          <Progress percent={pct} status="active" />
          <p style={{ textAlign: 'center', color: 'var(--color-text-secondary)', marginTop: 8 }}>
            分片上传中 {pct}%
          </p>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 编译验证**

Run: `cd frontend/packages/doctor-portal && npm run build`
Expected: tsc + vite build 成功退出

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/doctor-portal/src/components/BatchUploader.tsx
git commit -m "feat: BatchUploader 常驻上传卡片组件"
```

---

### Task 5: `components/BatchDetailPanel.tsx` —— 批次失败明细 + 重试

**Files:**
- Create: `frontend/packages/doctor-portal/src/components/BatchDetailPanel.tsx`

**Interfaces:**
- Consumes: `api: ApiClient`;`GET /reports/batches/{id}`;`POST /reports/batches/{id}/retry`
- Produces: `BatchDetailPanel({ api, batchId, onChanged }: { api: ApiClient; batchId: string; onChanged: () => void })`
  - mount 拉详情 → 失败文件表(文件/失败类型/原因);`failing>0` 时底部右侧「重试全部可重试」
  - 重试成功按 `requeued/skipped_unretryable` toast 并调 `onChanged()`(父页 `wake()` + 刷新历史)

- [ ] **Step 1: 新建组件**

Create `frontend/packages/doctor-portal/src/components/BatchDetailPanel.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { Button, message, Spin, Table, Tag, Tooltip } from 'antd';
import type { ApiClient } from '@hospital/shared';
import type { BatchDetail, FailingFile } from '../types/batch';
import { STAGE_LABEL, UNRETRYABLE_STAGES } from '../types/batch';

const failColumns = [
  { title: '文件', dataIndex: 'file_path', key: 'file_path', width: '45%' },
  {
    title: '失败类型', dataIndex: 'failed_stage', key: 'failed_stage', width: 160,
    render: (s: string | null) => (
      <Tag color={UNRETRYABLE_STAGES.has(s || '') ? 'red' : 'orange'}>
        {s ? (STAGE_LABEL[s] || s) : '失败'}
      </Tag>
    ),
  },
  { title: '原因', dataIndex: 'error_message', key: 'error_message' },
];

interface Props {
  api: ApiClient;
  batchId: string;
  onChanged: () => void;
}

export default function BatchDetailPanel({ api, batchId, onChanged }: Props) {
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get(`/reports/batches/${batchId}`);
      setDetail(data);
    } catch {
      message.error('批次详情加载失败');
    } finally {
      setLoading(false);
    }
  }, [api, batchId]);

  useEffect(() => { void load(); }, [load]);

  const retry = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/reports/batches/${batchId}/retry`, {});
      const rq = data.requeued ?? 0;
      const sk = data.skipped_unretryable ?? 0;
      if (rq > 0) message.success(`已重投 ${rq} 个;跳过 ${sk} 个不可重试`);
      else message.warning(`无可重试文件;跳过 ${sk} 个不可重试`);
      onChanged();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '重试失败');
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Spin size="small" style={{ margin: 8 }} />;

  const failing: FailingFile[] = detail?.failing_files || [];
  const retryable = failing.some((f) => !UNRETRYABLE_STAGES.has(f.failed_stage || ''));

  return (
    <div style={{ padding: '8px 0' }}>
      <Table
        dataSource={failing} columns={failColumns} rowKey="id" size="small"
        pagination={false} locale={{ emptyText: '无失败文件' }}
        style={{ background: 'var(--color-surface)', borderRadius: 8 }}
      />
      {failing.length > 0 && (
        <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
          <Tooltip
            title={retryable ? '重投所有可重试的失败文件'
              : '命名/用户医院不匹配/超大 等不可重试,请改文件名后重新上传整批'}
          >
            <Button onClick={retry} loading={busy} disabled={busy} type="primary" size="small">
              重试全部可重试
            </Button>
          </Tooltip>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 编译验证**

Run: `cd frontend/packages/doctor-portal && npm run build`
Expected: tsc + vite build 成功退出

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/doctor-portal/src/components/BatchDetailPanel.tsx
git commit -m "feat: BatchDetailPanel 批次失败明细与重试"
```

---

### Task 6: 重构 `pages/BatchUploadPage.tsx` —— 三区合一并接线

**Files:**
- Modify: `frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx`(整体替换)

**Interfaces:**
- Consumes: `BatchUploader`(Task 4)、`BatchDetailPanel`(Task 5)、`useBatchTracker`(Task 3)、`types/batch.ts`(Task 2)
- Produces: `/batch` 页 = 上传卡片(常驻)+ 处理中卡片区(实时轮询、自动恢复、可展开失败、取消)+ 历史批次表(分页、可展开失败/重试)
  - 历史表当前页行过滤掉活跃 id(避免双显示);活跃批次转终态 → toast + 历史表刷新
  - `GET /reports/batches?page=&page_size=20` 历史分页

- [ ] **Step 1: 整体替换页面文件**

Replace entire content of `frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Progress, Spin, Table, Tag, message } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
import BatchUploader from '../components/BatchUploader';
import BatchDetailPanel from '../components/BatchDetailPanel';
import { useBatchTracker } from '../hooks/useBatchTracker';
import type { BatchSummary } from '../types/batch';
import { STATUS_COLOR } from '../types/batch';

const PAGE_SIZE = 20;

const fmtTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-';

export default function BatchUploadPage() {
  const { api } = useDoctorStore();
  const [rows, setRows] = useState<BatchSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set());

  const reloadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const { data } = await api.get('/reports/batches', {
        params: { page, page_size: PAGE_SIZE },
      });
      setRows(data.items || []);
      setTotal(data.total ?? 0);
    } catch {
      message.error('历史批次加载失败');
    } finally {
      setHistoryLoading(false);
    }
  }, [api, page]);

  const handleSettled = useCallback((b: BatchSummary) => {
    if (b.status === 'completed') message.success('批量处理完成');
    else if (b.status === 'partial_failed') message.warning('部分文件失败,可在下方查看并重试');
    else if (b.status === 'cancelled') message.info('批次已取消');
    void reloadHistory();
  }, [reloadHistory]);

  const tracker = useBatchTracker(api, handleSettled);
  const { active, loading: activeLoading, error: activeError, wake } = tracker;

  const refresh = useCallback(() => {
    wake();
    void reloadHistory();
  }, [wake, reloadHistory]);

  const handleCreated = useCallback(() => { refresh(); }, [refresh]);
  const handleChanged = useCallback(() => { refresh(); }, [refresh]);

  useEffect(() => { void reloadHistory(); }, [reloadHistory]);

  const cancelBatch = async (bid: string) => {
    try {
      await api.post(`/reports/batches/${bid}/cancel`, {});
      refresh();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '取消失败');
    }
  };

  const toggleCard = (bid: string) => {
    setExpandedCards((prev) => {
      const next = new Set(prev);
      if (next.has(bid)) next.delete(bid); else next.add(bid);
      return next;
    });
  };

  const activeIds = new Set(active.map((b) => b.id));
  const displayRows = rows.filter((r) => !activeIds.has(r.id));
  const nightProcessing = active.some((b) => b.status === 'parsing' || b.status === 'interpreting');

  const columns = [
    { title: '文件名', dataIndex: 'filename', key: 'filename', ellipsis: true,
      render: (v: string) => <span title={v} style={{ display: 'block' }}>{v}</span> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 130,
      render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    { title: '解析', dataIndex: 'parsed_ok', key: 'parsed_ok', width: 70 },
    { title: '解读', dataIndex: 'interp_ok', key: 'interp_ok', width: 70 },
    { title: '失败', dataIndex: 'failed', key: 'failed', width: 70,
      render: (v: number) => (v > 0 ? <span style={{ color: 'var(--color-red)' }}>{v}</span> : v) },
    { title: '总数', dataIndex: 'total', key: 'total', width: 70 },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v?: string | null) => fmtTime(v) },
    { title: '完成时间', dataIndex: 'completed_at', key: 'completed_at', width: 160,
      render: (v?: string | null) => fmtTime(v) },
  ];

  return (
    <DoctorLayout>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 24 }}>📦 批量上传分发</h2>

        <div style={{
          border: '1px solid var(--color-border)', borderRadius: 12,
          padding: 24, background: 'var(--color-surface)', marginBottom: 24,
        }}>
          <h3 style={{ fontSize: 14, marginTop: 0 }}>上传新批次</h3>
          <BatchUploader api={api} onCreated={handleCreated} />
        </div>

        <h3 style={{ fontSize: 14, marginBottom: 8 }}>
          处理中批次 {active.length > 0 && `(${active.length})`}
        </h3>

        {activeLoading && <div style={{ padding: 16 }}><Spin /></div>}
        {activeError && (
          <Alert
            type="error" showIcon style={{ marginBottom: 16 }}
            message="活跃批次加载失败"
            action={<Button size="small" onClick={wake}>重试</Button>}
          />
        )}
        {!activeLoading && !activeError && active.length === 0 && (
          <p style={{ color: 'var(--color-text-secondary)', fontSize: 13, marginBottom: 16 }}>
            当前无处理中批次
          </p>
        )}

        {active.map((b) => {
          const done = (b.parsed_ok ?? 0) + (b.interp_ok ?? 0) + (b.failed ?? 0);
          const pct = b.total ? Math.round((done / b.total) * 100) : 0;
          return (
            <div key={b.id} style={{
              border: '1px solid var(--color-border)', borderRadius: 8,
              padding: '12px 16px', marginBottom: 12, background: 'var(--color-surface)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {b.filename}
                    </span>
                    <Tag color={STATUS_COLOR[b.status]}>{b.status}</Tag>
                  </div>
                  <Progress
                    percent={pct} status={b.status === 'partial_failed' ? 'exception' : 'active'}
                    size="small" style={{ margin: '8px 0 4px', maxWidth: 520 }}
                  />
                  <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                    {done}/{b.total} 文件 · 解析 {b.parsed_ok} · 解读 {b.interp_ok} · 失败 {b.failed}
                    {' · '}{fmtTime(b.created_at)}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {b.failed > 0 && (
                    <Button size="small" onClick={() => toggleCard(b.id)}>
                      {expandedCards.has(b.id) ? '收起失败' : `失败文件 (${b.failed})`}
                    </Button>
                  )}
                  <Button size="small" danger onClick={() => cancelBatch(b.id)}>取消</Button>
                </div>
              </div>
              {expandedCards.has(b.id) && b.failed > 0 && (
                <div style={{ marginTop: 12 }}>
                  <BatchDetailPanel api={api} batchId={b.id} onChanged={handleChanged} />
                </div>
              )}
            </div>
          );
        })}

        {nightProcessing && active.length > 0 && (
          <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 16 }}>
            批量任务在夜间 22:00–08:00 时段处理,白天可能停留在此状态,属正常。
          </p>
        )}

        <div style={{
          border: '1px solid var(--color-border)', borderRadius: 12,
          padding: 24, background: 'var(--color-surface)', marginTop: 24,
        }}>
          <h3 style={{ fontSize: 14, marginTop: 0 }}>历史批次</h3>
          <Table
            dataSource={displayRows} columns={columns} rowKey="id" loading={historyLoading}
            size="small" style={{ background: 'var(--color-surface)', borderRadius: 8 }}
            pagination={{
              current: page, pageSize: PAGE_SIZE, total, showSizeChanger: false,
              onChange: (p) => setPage(p),
            }}
            expandable={{
              rowExpandable: (r) => (r.failed ?? 0) > 0,
              expandedRowRender: (r) => (
                <BatchDetailPanel api={api} batchId={r.id} onChanged={handleChanged} />
              ),
            }}
            locale={{ emptyText: '暂无批次记录' }}
          />
          {displayRows.length > 0 && (
            <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 8 }}>
              展开含失败文件的批次可查看失败明细并重试。
            </p>
          )}
        </div>
      </div>
    </DoctorLayout>
  );
}
```

`api` 来自 `useDoctorStore()`,其类型即 `ApiClient`(两者同为 `AxiosInstance`),直接透传给子组件。

- [ ] **Step 2: 编译验证**

Run: `cd frontend/packages/doctor-portal && npm run build`
Expected: tsc + vite build 成功退出

- [ ] **Step 3: 后台代码一致性回归**

Run: `cd backend && .venv/bin/python -m pytest tests/test_batch_router.py tests/test_batch_service.py tests/test_batch_cross_hospital.py -q`
Expected: 全部 PASS(后端无功能改动,防误碰)

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx
git commit -m "feat: /batch 页重构支持历史列表、刷新自动恢复轮询与多批次并行"
```

---

### Task 7: 手工验收清单

无代码变更,人工在运行中的医生端验证(前置:先 `cd frontend/packages/doctor-portal && npm run build` 产物已生效,或 `npm run dev`)。

- [ ] **Step 1: 走查清单**

| # | 场景 | 预期 |
|---|------|------|
| 1 | admin 进 `/batch`,连续上传 2 个 zip | 两个批次同时在「处理中」各自显示进度,互不阻塞 |
| 2 | 处理中刷新页面 | 两批次自动恢复轮询并继续推进(本次修复验收点) |
| 3 | 一批完成 | success 提示;该批从「处理中」移到「历史批次」;另一批仍在处理中 |
| 4 | partial_failed 批次展开 | 失败明细表;「重试全部可重试」投递,unretryable 行提示不可重试 |
| 5 | 处理中批次点「取消」 | 批次立即从「处理中」消失;历史出现 cancelled 行 |
| 6 | 历史批次表翻页 | 分页正常,当前页不含仍在处理中的批次 |
| 7 | 全部批次终态后等待 ≥6s | 「处理中」区无新网络请求(停轮询,浏览器 Network 面板核对) |
| 8 | 活跃批次加载失败(临时断网) | 出现 error Alert,点「重试」恢复 |

- [ ] **Step 2: 记录结果**

如某项不符,回到对应 Task 修复并重跑 `npm run build`;全部通过则本计划完成。

---

## Self-Review 记录(填于计划编写时)

- **Spec 覆盖**:§1.1/§1.2 → Task 1;§2.1 类型/常量 → Task 2;§3 useBatchTracker → Task 3;§2.2 上传卡片 → Task 4;§2.3/§2.4 展开失败+重试 → Task 5;§2 三区整合接线 → Task 6;§4/§5 边界与验证 → Task 7。
- **无占位符**:所有文件改动均含完整代码;无 TBD。
- **类型一致性**:`ApiClient` 全链路统一;hook 返回 `{active, loading, error, wake}`;`BatchSummary`/`BatchDetail`/`FailingFile` 字段与后端响应字段逐一对齐;`ACTIVE_QUERY` 与后端多 status 语法一致(手动拼查询串,规避 axios 数组序列化为 `status[]` 的差异)。
