# 结论侧"重提取 vs 基线"护栏 — 设计

2026-09-15

## 背景与目标

指标侧已有"重提取 vs DB 验收快照"护栏（`tests/modules/report/test_gx_bj_indicator_guard.py`
+ 2026-09-15 新增的 `TestSummBaselineYellow`），能在报告重跑前发现代码回归。
结论侧目前只有 DB 快照对比（`test_h003_baseline_guard.py` / `test_h004_baseline_guard.py`
三件套 ②③：conclusion_text 哈希、总检异常条目名单），**发现不了"代码改了但 DB 还没重跑"**
的回归。

本设计新增结论侧 conclusion_text 的重提取护栏：对 26 份已适配报告（H003 13 + H004 13），
用当前代码从 PDF 确定性重提取结论段文本，与 DB 验收快照的 `conclusion_hash` 比对。

## 现状发现（2026-09-15 实测）

以 09-12 生成的 `baselines/h003_baseline.json` / `h004_baseline.json` 为基线，
对 26 份做确定性重提取（无 LLM 分支）：

- **MATCH 13 份**（hash 一致）：h003 2/3/4/7/26；h004 1/2/3/6/21/22/23/24
- **PENDING 11 份**（hash 不同，全部由本会话未提交的 `service.py` 结论链改动引起；
  文件级 bisect 证明与 `table_extractor.py` 等其它文件无关）：
  - 清理型 7 份：h003-1/6/20/23、h004-27（去签名尾行/页眉/单位碎片）；h003-22、h004-25（全角冒号→半角）
  - 待评审 4 份：h003-5（插入"超重本次体检总结：健康指导建议："）、
    h003-25（插入"13、[CT提示：肺结节]"）、h004-5（重复句）、
    h004-26（影像科附录扩张 + 条目删除）
- **SKIP 2 份**（无锚点段，结论经 OCR+LLM/VLM 抽取，非确定）：h003-24 福建第二、h004-4 欧阳庆

## 设计

### 文件与映射

新文件 `backend/tests/modules/report/test_conclusion_text_reguard.py`。

26 份报告 id → PDF 路径映射（h003 1-7,20,22-26；h004 1-6,21-27）：
- 广西体检报告测试/（h003 1-6；h004 1-3,5）
- 各地汇总/（h003 20,22-26；h004 21-27）
- 仓库根样本（h003-7 陈美杉、h004-6 步新宇）

### 重提取链（与生产 `_extract_conclusion_async` 同构，无 LLM 分支）

1. `_extract_pdf_text(pdf, hybrid=False)`
2. `_strip_ocr_html(text)`
3. `_locate_findings_sections(..., extra_break_re/extra_skip_re/extra_anchor_re=profile 编译,
   anchor_only)`（生产还传 `skip_lines={parsed_name, name}`；实现里省略——实测对 26 份
   结果无影响，且避免测试依赖 DB）
4. 段存在且 len>50 时：`table_conclusion` → `_reflow_table_conclusion`；
   否则 `_reflow_conclusion_lines`（+ `review_block` 时 `_drop_review_block`）
5. `[:16000]`；归一化 `re.sub(r"\s+", "", t)`；`sha1[:16]`

### 断言分组（显式名单，防静默丢覆盖）

- `MATCH`：严格 `hash == baseline.conclusion_hash`（13 份）
- `PENDING`：锁定"当前重提取 hash"（11 份），xfail 注明"待评审"；
  **任何再漂移（hash ≠ 记录值）即测试失败**，防止静默变化
- `SKIP`：`pytest.skip` 注明"结论经 LLM/VLM 抽取，无确定性重提取"（2 份）

若某报告 anchor 意外失效，不再落入 SKIP（名单固定），而是断言失败——覆盖不会静默消失。

### 不覆盖范围

总检异常条目名单（source=conclusion）由 LLM 生成，非确定，**不在本护栏内**；
仍由三件套 DB 快照守。若要覆盖，需引入 LLM 快照重放机制（`ABNORMALITY_LLM_SNAPSHOT_DIR`），
另立项。

## 后续任务（本护栏落地后）

1. 逐份评审 PENDING 11 份的 conclusion_text 差异：清理型确认后随重跑更新基线；
   待评审 4 份先查清是修复还是回归。
2. 德宏重跑（指标侧 LDL 判黄已验收）+ PENDING 中确认接受的报告批量端到端重跑，
   重生成 `baselines/*.json`，把 PENDING 移入 MATCH。
3. （可选）总检异常条目的快照重放护栏。

## 验证

- `cd backend && .venv/bin/python -m pytest tests/modules/report/test_conclusion_text_reguard.py -q`
- 全量：`pytest tests/modules/report/ tests/test_safety_net.py -q`

## 2026-09-15 评审更新（11 份 PENDING 逐份评审）

评审中发现并修复 4 处回归（3 处本会话引入、1 处既存），4 份由 PENDING 转 MATCH：

| 报告 | 定性 | 处理 |
|------|------|------|
| h004-5 钦州二 | 既存 bug：`_locate_findings_sections` 的"锚点前编号条目并入"未按注释限制"仅第一段"→ 同一行被相邻段重复收集 | 修：`if i == 0 and start > 0` |
| h004-25 茂名 | 本会话回归：reflow 新增"冒号结尾段头后正文另起一行"拆出非 ★ 内容行 → `_drop_review_block` 误判块尾 → 检查综述整段漏剥 | 修：块内非 ★ 次行按续行丢弃，块尾只认 ★ 行 |
| h003-22 滨州 / h004-25 茂名 | 本会话回归：`_split_label_explanation` 把源文本半角 `:` 统一改写为全角 `：`（6 处） | 修：拆分时保留源冒号字符 |
| h003-1 崇左 | 本会话回归：蔡超英文残行跳过规则误吞单位行（`mmol/L`/`U/L`） | 修：含 `/`、数字或 `%` 的短行不跳 |
| h003-5 防城港中 | 护栏链缺失：生产对 `visual_sort` 档案会按视觉坐标重排后再切段 | 修：护栏链补 `visual_sort` 重提取 |

评审后分组：**MATCH 18 / PENDING 6 / SKIP 2**。剩余 6 份 PENDING 均为
"清理型/代际差"（去页眉/签名尾/单位碎片、标题归位、去 `体检号` 页眉；其中
h003-20/23、h004-26 在 HEAD 即与基线不同 = 基线代际差），建议随批量重跑
（德宏 + 这 6 份）重生成 `baselines/*.json` 后移入 MATCH。

## 2026-09-15 收口（用户核对后的第二轮修复 + 基线重生成）

用户在重跑结果核对中报出 4 类问题，全部修复（均带纯函数断言）：

| 报告 | 根因 | 修法 |
|------|------|------|
| 潮州 | 结论签名行后仍收集附录锚点（`指标:DOB值` → C13 图表页垃圾） | 医生签名行之后的锚点不再收集 |
| 德宏 | 页脚重复 `建议：` 伪锚点（在签名行后） | 同上；结论止于"运输氧和二氧化碳。" |
| 马鞍山 | `_pair_arrow_by_row` 结果扫描从最左单元格开始，把序号（9/21/23）当结果 | 跳过名称左侧单元格 |
| 齐鲁 | ①`43岁`/医院名页眉未跳；②reflow 不合并未闭合 `【…` 标题 | 页眉跳过补两条规则 + 未闭合括号续行合并 |
| 茂名 | 检查综述（插入页）割断建议句，reflow 把续行并到综述数据行尾 | reflow `]`/`】` 结尾不并续行 + `_drop_review_block` 块尾放宽、续行归位接回 |

另修副作用（医院名页眉误入结论：崇左/防城港一/茂名）并给
`scripts/e2e_rerun_reports.py` 加 reprocess 预检（原文缺失跳过、不 wipe——
当日两次"先清后崩"事故的加固）。

**重跑与收口**：批次 1（6 份）→ 批次 2（7 份，含副作用报告）→ 批次 3（茂名结论句）→
`gen_h003/h004_baseline.py` 重生成基线 → 评审 JSON diff（仅预期 8 份变化）→
指标护栏德宏 xfail 移除。**终态：MATCH 24 + HYBRID 2（扫描件），三护栏与全量套件全绿
（448 passed / 5 skipped / 0 xfailed）。** 两批重跑完整 diff 存档 `log/`（gitignore）。
