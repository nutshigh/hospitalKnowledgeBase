# Task 10 Report: 文档更新(AGENTS.md + 旧 spec 标注)

## Status: DONE

## What was implemented

1. **AGENTS.md — `failed_stage` 取值段更新**(新 tenant 初始化必读节):
   - 已知取值追加 `/ hospital_not_found`,与任务 1(extract_worker resolver)落地对齐。
   - `oversize` 说明保留原有「无 `report_task_id` / UI 禁用重试」措辞(比 brief 更详细,兼容不冲突)。
   - `dispatch_unmatched` 描述从旧的「三段 `<姓名>_<医院编号>_<用户编号>`、末段纯数字」改为新约定「两段下划线、末段 5 位数字 + 末位 0-9/X」。
   - 新增 `hospital_not_found` 条目:文件名格式合法,但 `EXTERNAL_RESOLVER_URL` 无匹配或 hospital_id 本地未注册,不可重试。
   - 收尾行「后端 `retry_failed` 把这两类统称 unretryable」更新为「这三类」。

2. **AGENTS.md — 新增「## 批量上传文件名约定(2026-09-01 起)」小节**:
   - 置于新 tenant 节之后、日志收口节之前,与批量上传主题相邻。
   - 内容:命名规则、user_id 后六位锚定(VARCHAR(16))、`platform_user.id_card_suffix` + JWT 带出、`EXTERNAL_RESOLVER_URL` 契约指向新 spec §3、旧三段命名废弃说明。

3. **旧 spec 标注废弃**:
   - `docs/superpowers/specs/2026-07-16-batch-dispatch-by-filename-design.md` 标题下方加 blockquote:`> **已废弃(2026-09-01)**:命名约定改为 `<姓名>_<身份证后六位>`,见 `2026-09-01-batch-upload-idcard-suffix-design.md`。`
   - 文件正文未做其他改动(保留历史规范全文,便于对照)。

## Files changed

- `AGENTS.md`(failed_stage 段更新 + 新增文件名约定小节)
- `docs/superpowers/specs/2026-07-16-batch-dispatch-by-filename-design.md`(顶部废弃标注)

## Verification

- `cd backend && .venv/bin/pytest tests/ -q` → **256 passed, 2 failed**(2 个失败为 pre-existing:`test_logging_config` freezegun/transformers quirk 与 `test_group_sql.py::test_high_risk_list_basic`,与文档改动无关,符合预期基线)。
- 确认新 spec 文件 `docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md` 存在,引用路径有效。

## Self-review findings

- brief 的 oversize 文案不含「(UI 禁用重试按钮)」,我保留了 AGENTS.md 原有该说明;内容不冲突,信息更全。
- failed_stage 取值行与 AGENTS.md 顶部目录无索引项,无需同步其他交叉引用。
- 旧 spec 中 §2 命名规范等正文仍是旧三段规则,因已加顶部废弃 blockquote,保留原文作为历史记录是有意为之,不是遗漏。

## Concerns

- 无。未执行任何 git 写操作(manual commit mode 遵守)。
