# Task 10 Report: doctor-portal 报告详情随访问卷区块

## Implemented
- Created `frontend/packages/doctor-portal/src/components/FollowupPanel.tsx` verbatim from brief Step 1: desktop `Card`, GET `/followup/by-report/{reportId}` via `useDoctorStore` api, shows status `Tag`(已填写/待填写) + overall_level risk `Tag` + 提交时间 + per-question answers(`Array` 用「、」join,空值显 `—`);loading 时渲染 loading Card,无随访数据时渲染 null;空问卷渲染 `Empty`。
- Modified `frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx`:import `FollowupPanel`(第 7 行),并在 `InterpretationReportCard` 之后、`</DoctorLayout>` 之前渲染 `<FollowupPanel reportId={Number(id)} />`(第 95 行)。
- 未创建任何 `.js` twin(doctor-portal 无该惯例)。

## Typecheck
`pnpm exec tsc -p packages/doctor-portal/tsconfig.json --noEmit`(repo root,`cd frontend`)→ 无 TS 输出,通过(仅 pnpm workspaces 字段的 WARN,非错误)。

## Files changed
- `frontend/packages/doctor-portal/src/components/FollowupPanel.tsx`(new)
- `frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx`

## Commit
`dd977cc` feat(doctor-portal): 报告详情随访问卷区块(状态 + 答卷) — branch `feat/user-notification`(当前 HEAD 分支)。

## Self-review
- 组件逐字转录 brief Step 1,无偏差。
- 接线位置与 brief Step 2 一致:在 `InterpretationReportCard` 块后、`</DoctorLayout>` 前。
- 无随访数据(api 404/空)→ `data=null` → 渲染 null,不占版面;加载态有 loading Card。
- 依赖数组 `[reportId, api]` 正确,切换报告时重置 `loaded=false`。

## Concerns
- 组件 import 了 `Spin` 但未使用(转录自 brief);当前 doctor-portal tsconfig 未开 `noUnusedLocals`,typecheck 通过,故按 brief 逐字保留,未改动。
- 页面轮询期间(报告仍 loading 时页面整体 `<Spin/>`,未达 `<FollowupPanel/>`);随访问卷仅拉取一次,不随页面 10s 轮询刷新,已填写状态变化需刷新页面(与 user-portal 行为一致,符合预期)。
