# 医生端批量上传:历史任务列表 + 刷新自动恢复轮询 + 多批次并行 设计

**日期**:2026-09-03
**状态**:Draft(已与用户对齐 §1/§2/§3,待 review)
**前置**:
- 批量上传基础设施:`docs/superpowers/specs/2026-07-15-batch-report-upload-design.md`
- 批量上传 + 文件名分发:`docs/superpowers/specs/2026-07-16-batch-dispatch-by-filename-design.md`
- 身份证后六位双锚定:`docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md`
- 工程约束:`AGENTS.md`

---

## 0. 目标与边界

### 目标
修复「医生端批量上传后刷新页面丢失处理进度」的缺陷,并补齐两类能力:

1. **历史任务查看**:医生端(admin)可查看该医院过去所有批量上传批次的状态/进度/失败明细,并执行重试/取消。
2. **刷新自动恢复轮询**:页面刷新后自动发现并恢复对**未终态**批次的实时轮询。
3. **多批次并行**:多个批次可同时处于处理中,各自独立展示进度;上传区在其它批次处理期间仍可发起新上传。

### 根因(现状缺陷)
`BatchUploadPage.tsx` 把进度全部存在组件内存状态(`phase`/`progress`/`batchIdRef`),`batchIdRef` 为 `useRef`,刷新即失;且无 on-mount 恢复逻辑。后端 `BatchImport` 行与状态持久存在,数据并未丢失,纯粹是前端刷新后「找不到批次、不轮询、不渲染」。历史列表能力 `GET /reports/batches` 已存在但页面从未使用。

### 范围内
- `GET /reports/batches` 支持多 status OR 过滤,list 项补 `completed_at`
- 医生端 `/batch` 页重构:常驻上传卡片 + 处理中批次卡片区 + 历史批次表(展开失败明细/重试/取消)
- 新增前端 hook `useBatchTracker`(活跃批次发现 + 每 5s 轮询 + 自动恢复)
- 后端新增对应 pytest 用例

### 范围外(YAGNI)
- 前端单测基建(仓库现无 vitest/jest → 以 `tsc` build + 手工清单验证)
- 用户级隔离(活跃轮询与历史沿用 list 语义,全医院可见)
- `uploading` 中途失败批次的 UI 恢复(由 sweeper 超时回收,见 §4.1)
- 单 tab 同时进行两个「分片上传」流程(保留单飞,不阻塞其它批次处理/轮询)

---

## 1. 后端改动

仅改 `backend/app/modules/report/batch_router.py::list_batches`(现状见该文件 92-112 行)。

### 1.1 status 多值 OR 过滤
- 参数签名:`status: Optional[List[str]] = Query(None)`
- 过滤逻辑:提供了 status 列表时 `BatchImport.status.in_(status)`,否则不过滤
- 向后兼容:既有调用方传单个 `?status=x` 时 FastAPI 解析为 `["x"]`,行为不变
- 新增 list 项字段:`completed_at`(`b.completed_at.isoformat() if b.completed_at else None`),用于历史展示

> 现状 list 每项只回 `id/filename/status/total/parsed_ok/interp_ok/failed/created_at`;活跃轮询所需的进度计数(parsed/interp/failed/total)list 已带,无需逐批次请求。

不新增 endpoint、不动 models / DDL / 跨院分发逻辑(BatchImport 行与状态存于上传方库,list 本就按上传方 `hospital_id` 过滤)。

### 1.2 测试
`backend/tests/test_batch_router.py` 新增:
- 构造不同 status 的批次行(含 completed / partial_failed / parsing / interpreting)
- `?status=parsing&status=interpreting` 只回两者(OR)
- 单个 `?status=completed` 行为不变
- list 项含 `completed_at`(completed 有值、未终态为 null)

---

## 2. 前端结构(医生端 `/batch` 单页)

`frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx` 重构为三个纵向区域,上传区不再被上传过程独占。

### 2.1 状态与常量
- `TERMINAL = ['completed', 'partial_failed', 'cancelled']`
- `ACTIVE = ['extracting', 'parsing', 'interpreting']`
- 数据模型:
  - `BatchSummary`:来自 list(id/filename/status/total/parsed_ok/interp_ok/failed/created_at/completed_at)
  - `BatchDetail`:来自 `GET /reports/batches/{id}`(`batch` + `failing_files`)
- 常量/状态色/失败阶段文案沿用现页(STATUS_COLOR/STAGE_LABEL/UNRETRYABLE_STAGES)

### 2.2 上传卡片(常驻顶部)
拆独立组件 `BatchUploader`,职责仅「选文件 → 分片上传 → complete」,保留现有分片逻辑(CHUNK_SIZE 5MB、expected_total/expected_size)。
- 上传中按钮禁用,同一 tab 同时最多一个上传流程;上传期间下方列表/轮询照常
- 命名约定提示仍展示(现页有 `!busy` 才隐藏,新结构常驻或可折叠)
- `complete` 成功后回调 `onCreated(batchId)` → 由页面触发 `tracker.wake()` 立即拉取新批次入处理中区

### 2.3 处理中批次卡片区(活跃)
由新 hook `useBatchTracker(api)` 驱动,见 §3。
每张卡片展示:文件名 + 状态 Tag + `Progress`((parsed_ok+interp_ok+failed)/total)+ 三计数 + 创建时间;`failed>0` 展开失败文件 → 单批 retry;取消按钮(非终态)。
批次转入终态(completed/partial_failed/cancelled)时:`message` 提示,批次从卡片区移除(自然落进下方历史表)。

### 2.4 历史批次表
- 数据:`GET /reports/batches?page=&page_size=20`,antd Table 分页
- 列:文件名 / 创建时间 / 状态 / 进度计数 / 完成时间
- **去重**:当前活跃批次(id ∈ tracker.active)从本页渲染中过滤,避免同批次双显示;活跃批次进入终态后自动出现在历史页(终态变化时 `wake()` 触发表格刷新)
- 行展开:按需 `GET /reports/batches/{id}` 取 `failing_files`,展示失败明细(failed_stage + error_message);操作:
  - 重试全部可重试(partial_failed/completed 且存在可重试文件;unretryable 阶段工具提示说明不可重试)
  - 取消(仅非终态行,理论不可达因非终态已被过滤进活跃区,保底逻辑)
- 展开详情网络失败:行内显示失败与重试入口,不崩页面

---

## 3. useBatchTracker hook(核心修复点)

新文件 `frontend/packages/doctor-portal/src/hooks/useBatchTracker.ts`。

```ts
useBatchTracker(api): {
  active: BatchSummary[];     // 非终态批次,按 created_at desc
  loading: boolean;
  error: boolean;
  wake: () => void;           // 立即触发一次拉取(新批次/retry/cancel 后调用)
}
```

行为:
1. **mount 自动恢复**:进入 `/batch` 页即 `GET /reports/batches?status=extracting&status=parsing&status=interpreting&page_size=100` 一次,发现刷新前遗留的未终态批次并恢复轮询。
2. **轮询节奏**:每 5s 单请求(一次拉全活跃,不再逐批次 N 请求);本次返回无活跃则停表(避免空转),有活跃才安排下个 tick;`wake()` 重置计时立即刷新。
3. **合并更新**:按 batch id 覆盖更新;批次掉出活跃集(终态/不存在)即从 `active` 移除并触发终态回调。
4. **清理**:组件卸载清定时器。
5. 网络抖动:catch 后继续下一 tick(沿用现页容错);停表/无活跃时不轮询。

终态变化通过页面层监听 active 前后 diff 触发:success 提示 / warning 提示 + 历史表刷新。

> 注:list 接口按 admin + 上传方医院过滤,故恢复范围为**该医院**全部非终态批次(非仅当前登录 admin 个人上传)。与 dispatch 管理页的可见性语义一致,接受此边界(§0 范围外)。

---

## 4. 边界场景

1. **刷新发生在分片上传中途**:批次留 `uploading`,不进活跃列表(ACTIVE 不含),由 sweeper 在 `BATCH_CHUNK_TIMEOUT` 后回收(见 `app/core/batch_sweeper.py::_sweep_reaper`);页面回到可重新上传态,无幽灵进度。
2. **多管理员同院**:活跃轮询与历史表沿用 list 语义,全医院可见/可恢复,不做按人隔离。
3. **夜间窗口长时间停留 parsing/interpreting**:只要 ≥1 个活跃批次,卡片区持续每 5s 轮询,进度保持可见。
4. **retry 使 partial_failed → parsing/interpreting**(后端 `retry_failed` 已处理状态回退):`wake()` 后该批次从历史表移到处理中卡片;cancel 立即从卡片区移除。
5. **展开行/网络失败**:详情加载失败不崩页面,显示重试入口。
6. **活跃上限**:单请求 `page_size=100`(接口上限),超过 100 个同时活跃的场景(夜间窗口批量上报高峰)不在本次考虑,list 本身分页仍可翻。

---

## 5. 验证

### 5.1 后端
- 定向跑 `pytest backend/tests/test_batch_router.py`(含新增多 status OR + completed_at 用例)
- 仓库其它测试回归:`pytest backend/tests/test_batch_service.py backend/tests/test_batch_cross_hospital.py`(确认 list 改动无副作用)

### 5.2 前端
- 无单测基建 → `cd frontend/packages/doctor-portal && npm run build`(tsc + vite)
- 手工清单(admin 登录进 `/batch`):
  1. 连续上传 2 个 zip → 两个批次同时在「处理中」各自推进,不互阻塞
  2. 处理中刷新页面 → 两批次自动恢复轮询并继续显示进度(修复验收点)
  3. 一批完成 → success 提示,卡片移到历史表;另一批仍在卡片区
  4. partial_failed 批次展开失败明细 → 重试全部可重试;unretryable 文件置灰提示
  5. 取消非终态批次 → 立即从卡片区消失
  6. 历史表翻页 / 展开历史行查看失败明细
  7. 所有批次终态后卡片区停轮询(无空转请求)

---

## 6. 文件清单

| 文件 | 动作 |
|------|------|
| `backend/app/modules/report/batch_router.py` | list_batches:status 多值 + completed_at |
| `backend/tests/test_batch_router.py` | 新增多 status OR + completed_at 用例 |
| `frontend/packages/doctor-portal/src/hooks/useBatchTracker.ts` | 新增活跃批次轮询 hook |
| `frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx` | 重构:上传卡片 + 处理中卡片区 + 历史表 |
| `frontend/packages/doctor-portal/src/components/BatchUploader.tsx`(新) | 上传流程独立组件(选文件 → 分片 → complete,回调 onCreated) |
