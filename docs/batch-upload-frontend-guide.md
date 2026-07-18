# 批量上传体检报告 — 前端接入指导

> 给下一个前端 Agent:本文假设你已读过 `batchToDo.md` 与 `docs/superpowers/specs/2026-07-15-batch-report-upload-design.md`。目标是在现有前端里接入"管理员批量上传 zip/tar 包 → 自动解析+解读 → 轮询进度 → 失败重试"的完整闭环。

---

## 0. 一句话总览

后端已提供 8 个 REST 接口(挂在 `/api/v1/reports` 下),采用 **三步流式分片上传 + 轮询** 模型。前端要做的核心是:把一个大 zip 切成 5MB 片、循环上传、调 complete 触发后台解压解析、然后轮询进度直到终态。**不需要前端调 OCR / LLM**,全部后台异步。

---

## 1. 认证与多租户(关键,先搞懂再写)

| 维度 | 说明 |
|------|------|
| 鉴权 | 所有接口需 `Authorization: Bearer <JWT>`(由 `createApiClient` 自动注入,见 `packages/shared/src/api/client.ts`) |
| 角色 | **必须 `role === "admin"`**,否则 `batch_router._db` 返回 403 |
| 租户 | `hospital_id` 从 JWT payload 取,**前端不要传**。每家医院是独立 MySQL 库 + 独立 `batch_import` 表 + 独立存储目录,天然隔离 |
| 谁来用 | "医院管理员"(role=admin 且 hospital_id 非空)。**不是** admin-portal 的"平台运营"(平台运营无 hospital_id,调 batch 会 400 "Hospital context required") |

### 放在哪个 portal?

**结论:放进 `packages/doctor-portal`**(它已经是医院作用域应用,登录时已捕获 `role`/`hospitalId`,有 Layout/sidebar/antd/zustand,与报告体系打通)。

`doctor-portal` 的 `LoginPage` 已经把 `res.data.role` 存进 store(`doctorStore.ts: setAuth(token, user_id, role, hospital_id)`),所以医院管理员用同一登录口进去,store 里 `role==='admin'`。新页面用 `role` 做菜单/路由守卫即可。

> 不要放进 `admin-portal`:那是平台级租户管理后台,登录用户无 `hospital_id`,调 batch 接口会被 `_db` 拦 400。

### 登录态检查

`doctorStore` 当前没有持久化 `role`/`userId`(只存了 token 到 localStorage)。建议顺手补一下持久化,否则刷新页面后 `role` 丢失、菜单守卫失效:

```ts
// packages/doctor-portal/src/stores/doctorStore.ts
// 在 setAuth 里把 role/userId/hospitalId 也写 localStorage;store 初始值从 localStorage 读回。
```

(若不想改 store,也可每次进页面用 `api.get('/auth/me')` 回填 `role`。二选一。)

---

## 2. 接口契约(权威,以 `backend/app/modules/report/batch_router.py` 为准)

所有路径前缀 `/api/v1/reports`。请求/响应字段名严格区分大小写。

### 2.1 创建批次
`POST /batches`
- Content-Type: `multipart/form-data`
- Form 字段:`filename`(string,如 `bench.zip`)
- 响应:`{ "batch_id": "<32位hex>" }`

### 2.2 上传分片(循环,序号 0 起)
`POST /batches/{batch_id}/chunk`
- Content-Type: `multipart/form-data`
- Form 字段:
  - `index`(int, **0-based**)
  - `total`(int, 总片数)
  - `data`(UploadFile, 本片二进制)
- 响应:`{ "received": index, "total": total }`

### 2.3 标记完成(触发 CRC 校验 + 解压 + 投递解析)
`POST /batches/{batch_id}/complete`
- Content-Type: `application/json`
- Body:`{ "expected_total": <片数>, "expected_size": <zip 字节数>, "expected_crc32": "<8位hex, 可选>" }`
- 失败码(400):`chunks_incomplete` / `crc_mismatch` / `archive_too_large`
- 成功响应:`{ "batch_id", "status": "extracting" }`

### 2.4 列表
`GET /batches?page=1&page_size=20&status=<可选>`
- 响应:`{ items: [{id, filename, status, total, parsed_ok, interp_ok, failed, created_at}], total, page, page_size }`

### 2.5 进度详情
`GET /batches/{batch_id}`
- 响应:
```json
{
  "batch": {
    "id","filename","status","total","parsed_ok","interp_ok","failed",
    "error_message","created_at","completed_at"
  },
  "failing_files": [{ "id","file_path","error_message" }]
}
```

### 2.6 死信列表
`GET /batches/{batch_id}/dead` → `{ "dead": [...] }`(DLQ 消息 7 天 TTL,见 §5)

### 2.7 重试失败
`POST /batches/{batch_id}/retry`
- Body(可选):`{ "file_ids": ["<fid>", ...] }`,不传则重投该 batch 所有 failed 文件
- 响应:`{ "requeued": <n> }`
- 后端按 `failed_stage` 自动路由:`parsing`/`oversize` → 重投 parsing.bulk;`interpretation` → 重投 interpretation.bulk(只重置解读行,不重跑 OCR)

### 2.8 取消
`POST /batches/{batch_id}/cancel` → `{ "cancelled": true }`
- 终态(`completed`/`partial_failed`)不可取消,后端返 400

### 状态机(轮询终止条件)
`uploading → extracting → parsing → interpreting → completed`
有失败 → `partial_failed`;取消 → `cancelled`。**前端轮询直到 status ∈ {completed, partial_failed, cancelled} 才停。**

---

## 3. 前端文件落点(doctor-portal)

| 文件 | 动作 | 说明 |
|------|------|------|
| `src/pages/BatchUploadPage.tsx` | 新建 | 上传向导:选 zip → 切片上传 → complete → 轮询进度(参考实现见 §6) |
| `src/pages/BatchListPage.tsx` | 新建 | 批次列表 + 进度 + 失败重试/取消(可合进 BatchUploadPage 一个 Tab) |
| `src/router.tsx` | 改 | 加 `<Route path="/batch" element={<AuthGuard><BatchUploadPage/></AuthGuard>} />`(建议加 `RoleGuard` 只放 admin 进) |
| `src/components/DoctorLayout.tsx` | 改 | `MENU` 加 `{ key: '/batch', label: '批量上传', icon: '📦' }`,且**仅当 `role==='admin'` 才渲染该项** |
| `src/stores/doctorStore.ts` | 改 | 持久化 `role`(见 §1);可加 `batchApi` 不必,直接用 `api` |
| `packages/shared/src/api/batch.ts` | 可选新建 | 把切片/轮询逻辑抽成可复用函数(见 §6 末尾),便于单测 |

### RoleGuard 参考
```tsx
function RoleGuard({ allow, children }: { allow: string[]; children: React.ReactNode }) {
  const role = useDoctorStore(s => s.role);
  if (!allow.includes(role)) return <Navigate to="/" replace />;
  return <>{children}</>;
}
// router: <Route path="/batch" element={<AuthGuard><RoleGuard allow={['admin']}><BatchUploadPage/></RoleGuard></AuthGuard>} />
```

---

## 4. 必须遵守的约束(踩坑高发区)

1. **分片 index 0 起**,不是 1 起。后端 `append_chunk` 用 `index` 直接拼 `.part{index}`,且 complete 时校验 `got_indices == range(expected_total)`。从 1 起 → `chunks_incomplete`。
2. **create 是 multipart Form 不是 JSON**:`-F filename=...`,别用 `-d '{"filename":...}'`。
3. **chunk 路径是单数 `/chunk`**,不是 `/chunks`;字段名是 `index`/`total`/`data`,不是 header `X-Chunk-Seq`。(`scripts/bench-batch.sh` 曾写错,已修,照修后版本。)
4. **complete 必须带 `expected_total` + `expected_size`**(JSON body),否则 400。
5. **文件类型白名单**:`.pdf .doc .jpg .jpeg .png`(**不含 .docx**,Spec F8)。zip 内 .docx 文件会被静默跳过。前端 `accept=".zip,.tar"` 选包,但要在 UI 提示包内只收上述类型。
6. **大小上限**:单包 ≤ 10GB(`BATCH_ARCHIVE_MAX_SIZE`);包内单文件 > 50MB(`BATCH_FILE_MAX_SIZE`)会被记 `failed_stage=oversize` 不解析。前端选包时给个软提示。
7. **chunk 建议大小 5MB**(`BATCH_CHUNK_SIZE` 默认),过小请求数爆炸,过大内存压力 + 重传成本高。
8. **bulk 时段窗口**:默认 22:00–08:00 才真正消费 parsing.bulk / interpretation.bulk。白天上传会停在 `parsing` 状态堆积,**这不是 bug**。UI 要在 `parsing`/`interpreting` 长时间不动时提示"夜间批量时段处理中"。`extract_worker` 不受时段限制,解压会立即跑。
9. **CRC32 是可选的**:`expected_crc32` 不传则跳过校验。要传的话用 `zlib.crc32` 算 8 位 hex(前端可用 `crc-32` npm 包,或不算、靠 `expected_size` 兜底)。
10. **401 处理已内置**:`createApiClient` 的响应拦截器遇 401 清 token 跳 `/login`,无需页面单独处理。
11. **不要在前端拼 `hospital_id`**:JWT 已带,后端 `_db` 自动取。传了也会被忽略(以 JWT 为准)。

---

## 5. 失败与重试 UX

- `failing_files` 列出每个失败文件 + `error_message`。`error_message` 为 `oversize` 的不可重试(文件太大,无 task);其它可点"重试"。
- 重试可选传 `file_ids`(批量勾选),或整批重试。
- 死信:解读/解析重试 3 次仍失败的消息进 DLQ,7 天后自动 drop。建议在批次详情页提供"查看死信"入口(`GET /dead`),并在 UI 标红"请在 7 天内处理"。
- `retry` 返回 `{requeued:n}`,前端据此刷新列表。重试后 status 会从 `partial_failed` 回到 `parsing` 或 `interpreting`,轮询继续。

---

## 6. 参考实现

风格对齐现有代码:antd v5、zustand、`useDoctorStore().api`、内联 style + CSS 变量(`--color-*`)、teal 主色。**不要引入新 UI 库。**

### 6.1 `BatchUploadPage.tsx`(核心:切片 + 上传 + 轮询)

```tsx
import { useState, useRef, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Upload, Button, Progress, message, Tag, Table } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

const CHUNK_SIZE = 5 * 1024 * 1024; // 5MB,与后端 BATCH_CHUNK_SIZE 对齐
const TERMINAL = ['completed', 'partial_failed', 'cancelled'];

const STATUS_COLOR: Record<string, string> = {
  uploading: 'default', extracting: 'blue', parsing: 'gold',
  interpreting: 'orange', completed: 'green', partial_failed: 'red', cancelled: 'default',
};

export default function BatchUploadPage() {
  const { api } = useDoctorStore();
  const nav = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'polling'>('idle');
  const [uploaded, setUploaded] = useState(0);   // 已上传字节数
  const [progress, setProgress] = useState<any>(null);
  const batchIdRef = useRef<string | null>(null);

  // ── 轮询 ──
  const poll = useCallback(async (bid: string) => {
    const tick = async () => {
      try {
        const { data } = await api.get(`/reports/batches/${bid}`);
        setProgress(data.batch);
        if (TERMINAL.includes(data.batch.status)) {
          setPhase('idle'); setBusy(false);
          if (data.batch.status === 'completed') message.success('批量处理完成');
          else if (data.batch.status === 'partial_failed') message.warning('部分文件失败,可重试');
          return;
        }
      } catch { /* 网络抖动,继续 */ }
      timer = window.setTimeout(tick, 5000);
    };
    let timer = window.setTimeout(tick, 3000);
  }, [api]);

  // ── 切片 + 上传 ──
  const start = async () => {
    if (!file) return;
    setBusy(true); setPhase('uploading'); setUploaded(0); setProgress(null);
    try {
      // 1. create
      const createForm = new FormData();
      createForm.append('filename', file.name);
      const { data: cd } = await api.post('/reports/batches', createForm);
      const bid = cd.batch_id as string;
      batchIdRef.current = bid;

      // 2. 切片循环上传(index 0 起)
      const total = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
      for (let i = 0; i < total; i++) {
        const blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
        const form = new FormData();
        form.append('index', String(i));
        form.append('total', String(total));
        form.append('data', blob, `${file.name}.part${i}`);
        await api.post(`/reports/batches/${bid}/chunk`, form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          onUploadProgress: (e) => setUploaded(Math.min(file.size, i * CHUNK_SIZE + (e.loaded || 0))),
        });
      }

      // 3. complete(expected_crc32 留空,靠 expected_size 兜底)
      await api.post(`/reports/batches/${bid}/complete`, {
        expected_total: total, expected_size: file.size,
      });

      // 4. 轮询
      setPhase('polling');
      await poll(bid);
    } catch (err: any) {
      const code = err?.response?.data?.detail;
      message.error(code ? `上传失败: ${code}` : '上传失败,请重试');
      setPhase('idle'); setBusy(false);
    }
  };

  const pct = file ? Math.round((uploaded / file.size) * 100) : 0;
  const done = progress && (progress.parsed_ok ?? 0) + (progress.interp_ok ?? 0) + (progress.failed ?? 0);
  const totalFiles = progress?.total ?? 0;

  return (
    <DoctorLayout>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 24 }}>批量上传体检报告</h2>

        {!busy && (
          <div style={{ border: file ? '2px solid var(--color-primary)' : '2px dashed var(--color-border)',
            borderRadius: 12, padding: '40px 20px', textAlign: 'center', background: 'var(--color-surface)' }}>
            <Upload.Dragger
              beforeUpload={(f) => { setFile(f); return false; }}
              showUploadList={false}
              accept=".zip,.tar,.gz,.tgz"
              style={{ background: 'transparent', border: 'none' }}
            >
              <InboxOutlined style={{ fontSize: 48, color: 'var(--color-text-secondary)', marginBottom: 16 }} />
              <p style={{ fontWeight: 600 }}>{file ? file.name : '点击或拖拽上传 zip/tar 包'}</p>
              <p style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                包内仅收 PDF/DOC/JPG/PNG(不含 DOCX),单文件 ≤ 50MB,整包 ≤ 10GB
              </p>
            </Upload.Dragger>
          </div>
        )}

        {file && !busy && (
          <Button type="primary" block size="large" onClick={start} disabled={!file}
            style={{ height: 48, marginTop: 24, background: 'var(--color-primary)', border: 'none' }}>
            开始上传
          </Button>
        )}

        {phase === 'uploading' && (
          <div style={{ marginTop: 24 }}>
            <Progress percent={pct} status="active" />
            <p style={{ textAlign: 'center', color: 'var(--color-text-secondary)', marginTop: 8 }}>
              分片上传中 {pct}%
            </p>
          </div>
        )}

        {phase === 'polling' && progress && (
          <div style={{ marginTop: 24 }}>
            <div style={{ marginBottom: 12 }}>
              <Tag color={STATUS_COLOR[progress.status]}>{progress.status}</Tag>
              <span style={{ marginLeft: 12, color: 'var(--color-text-secondary)' }}>
                {done}/{totalFiles} 文件 · 解析 {progress.parsed_ok} · 解读 {progress.interp_ok} · 失败 {progress.failed}
              </span>
            </div>
            <Progress
              percent={totalFiles ? Math.round((done / totalFiles) * 100) : 0}
              status={progress.status === 'partial_failed' ? 'exception' : 'active'}
            />
            <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 8 }}>
              {['parsing', 'interpreting'].includes(progress.status)
                ? '批量任务在夜间 22:00–08:00 时段处理,白天可能停留在此状态,属正常。'
                : '处理中,每 5s 自动刷新…'}
            </p>
          </div>
        )}

        {progress?.status === 'partial_failed' && (
          <div style={{ marginTop: 24 }}>
            <Button onClick={async () => {
              const { data } = await api.post(`/reports/batches/${batchIdRef.current}/retry`, {});
              message.success(`已重投 ${data.requeued} 个失败文件`);
              if (batchIdRef.current) { setPhase('polling'); setBusy(true); poll(batchIdRef.current); }
            }}>重试全部失败文件</Button>
          </div>
        )}
      </div>
    </DoctorLayout>
  );
}
```

### 6.2 抽成可复用 helper(可选,放 `packages/shared/src/api/batch.ts`)

```ts
import { AxiosInstance } from 'axios';
const CHUNK = 5 * 1024 * 1024;
const TERMINAL = ['completed', 'partial_failed', 'cancelled'];

export async function uploadBatch(
  api: AxiosInstance, file: File,
  onProgress?: (pct: number) => void,
): Promise<string> {
  const fd = new FormData(); fd.append('filename', file.name);
  const { data: { batch_id } } = await api.post('/reports/batches', fd);
  const total = Math.max(1, Math.ceil(file.size / CHUNK));
  for (let i = 0; i < total; i++) {
    const blob = file.slice(i * CHUNK, (i + 1) * CHUNK);
    const f = new FormData();
    f.append('index', String(i)); f.append('total', String(total)); f.append('data', blob, `p${i}`);
    await api.post(`/reports/batches/${batch_id}/chunk`, f);
    onProgress?.(Math.round(((i + 1) / total) * 100));
  }
  await api.post(`/reports/batches/${batch_id}/complete`, { expected_total: total, expected_size: file.size });
  return batch_id;
}

export async function pollBatch(api: AxiosInstance, bid: string, onUpdate: (b: any) => void, intervalMs = 5000) {
  return new Promise<void>((resolve) => {
    const tick = async () => {
      try { const { data } = await api.get(`/reports/batches/${bid}`); onUpdate(data.batch);
        if (TERMINAL.includes(data.batch.status)) return resolve(); } catch {}
      setTimeout(tick, intervalMs);
    };
    setTimeout(tick, 3000);
  });
}
```

> `onUploadProgress` 在 `api.post` 第三参 `AxiosRequestConfig` 里可用(`createApiClient` 返回的就是 axios 实例)。如果 shared 的 ts 类型没导出 `AxiosRequestConfig`,直接内联对象即可。

---

## 7. 验证清单(自测)

1. 用一个含 3 个 PDF 的小 zip 跑完整链路,最终 status=completed,`parsed_ok=3`。
2. 用一个 .docx 混入的 zip,确认 docx 被跳过、其它正常。
3. 上传中刷新页面:批次应停在 `uploading`,5 分钟后 `BatchSweeper` 不会立刻清(默认 2h 超时 `BATCH_CHUNK_TIMEOUT`),可在列表页恢复轮询。
4. 故意传一个 >50MB 单文件的 zip → 该文件 `failed_stage=oversize`,列表里标红且不可重试。
5. 白天上传 → status 停在 `parsing`,UI 显示夜间窗口提示。
6. 后端基线:`cd backend && .venv/bin/pytest tests/ -q` 必须 168 passed(改前端不动后端,不应回归)。

---

## 8. 不要做的事

| ❌ | 原因 |
|----|------|
| 在请求里带 `hospital_id` 字段 | 后端只信 JWT,传了被忽略;徒增误解 |
| 用 JSON 调 `/batches` create | 后端要 multipart Form `filename`,会 422 |
| index 从 1 起 | 后端 0 起,会 `chunks_incomplete` |
| 把批量上传放 admin-portal | 平台运营无 hospital_id,被 `_db` 拦 400 |
| 引入新 UI 库 / 新状态管理 | 现有 antd v5 + zustand 足够,保持一致 |
| 前端做 CRC32 强校验阻断上传 | `expected_crc32` 可选,没算就不传,靠 `expected_size` |
| 把单文件 >50MB 的文件当可重试 | oversize 无 task_id,重试无效;UI 应禁用其重试按钮 |

---

## 9. 参考索引

| 资源 | 路径 |
|------|------|
| 后端路由(权威契约) | `backend/app/modules/report/batch_router.py` |
| 后端状态机/重试逻辑 | `backend/app/modules/report/batch_service.py` |
| 设计 spec | `docs/superpowers/specs/2026-07-15-batch-report-upload-design.md` |
| 遗留待办 | `batchToDo.md` |
| 压测脚本(请求形状范例) | `scripts/bench-batch.sh` |
| 前端 API client | `frontend/packages/shared/src/api/client.ts` |
| 现有单文件上传(参考风格) | `frontend/packages/user-portal/src/pages/UploadPage.tsx` |
| 目标 portal 布局/菜单 | `frontend/packages/doctor-portal/src/components/DoctorLayout.tsx` |
| 工程/部署约束 | `AGENTS.md` |
