# 第八批报告适配（93-98）· 交接文档（给下一个 Agent）

> 本仓库：`/data/project/hospitalKnowledgeBase`（主 checkout，后端 8000）
> 生成时间：2026-10-20（第八批 11 份全部验收+冻结、提交 `2abba56` 之后）
> **动手前必读 `AGENTS.md` 的相关章节**（本文件是它的浓缩入口，冲突以 AGENTS.md 为准）

---

## 0. 你的任务

继续适配**下一批体检报告**（第九批，样本路径/份数待用户确认），按《新地区报告适配标准流程 v2》
（AGENTS.md 同名章节 + 本文件 §3）执行，产出：
①解析规则修复（数据化进 `report_profiles.py`）②新报告纳入三层基线护栏 ③AGENTS.md 记录 ④提交。

**用户协作方式**：用户在前端逐份验收，按 `医院报告：1.… 2.…` 给问题清单；
你负责：离线复现 → 修规则 → 跑全量护栏 → 汇报（含证据）→ 用户验收 → 冻结基线。
**端到端重跑（reprocess/reinterpret）只在用户明确发令后执行。**

**当前无待办批次**；本轮 93-98 已全部验收冻结。已知未实施项见 §1。

---

## 1. 当前状态（2026-10-20 收口）

| 项 | 现状 |
|---|---|
| 已适配并冻结的报告 | **88 份**：H003 基线 18 份 + H004 基线 70 份（`tests/modules/report/baselines/`） |
| 全量护栏 | `cd backend && .venv/bin/python -m pytest tests/modules/report/ tests/test_safety_net.py tests/core/ -q` → **937 passed / 52 skipped** |
| 结论重提取护栏 | **60 MATCH**（严格 hash==基线）+ **28 HYBRID 离线锁**（扫描/图片/无结论段，锁空或离线产物防漂移） |
| 指标重提取护栏 | 54 份文本可复现报告（`test_gx_bj_indicator_guard.py::_BASELINE_CASES`） |
| 样本库 | `conclusion_samples.py` 86 条（精修强断言 / `smoke` 冒烟 / `ocr` 需 OCR 默认跳过） |
| 最近提交 | `2abba56 feat(解析与基线)：第八批11份报告适配验收+基线护栏+崇左结论段修复` |
| 未提交（按约定永不提交） | `frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx`、`frontend/packages/user-portal/src/components/IndicatorRow.tsx`、`frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`、根目录 `background_example.jpg`、`body_background.jpg` |

**已知遗留（不属下一批，可顺带）**：
1. `unit_name=医院（体检机构）` + `exam_no`（体检编码）+ `exam_year`（仅年份）落库方案
   （口径已定，**未实施**：需 DDL 三处同步 + 提取器 + 回填 + 测试）。
2. 93 陈行富结论跨页续段依赖 `ocr_content_pages` 的 OCR；OCR 服务崩溃时会退回缺页文本
   （worker 现会抛 `failed_stage=ocr_failed` 可重试，但重跑前仍应先验 OCR）。
3. 前端 3 个文件 + 2 张 jpg 长期未提交（用户要求"非后端功能文件不提交"）。

---

## 2. 环境与工具速查（本 checkout 口径）

| 事项 | 命令/说明 |
|---|---|
| 服务启停 | `bash start.sh`（启动即返回）/ `bash start.sh --stop`（Docker 中间件不动） |
| 端口 | 后端 **8000**、embed 8002、reranker 8003、MedGo 8004、**OCR 8001**、前端 3001/3002/3003 |
| **worker 重启**（改 worker 链代码后必做） | `bash /tmp/restart_workers2.sh`（内容见附录 A；跑完 `ps -eo pid,lstart,cmd` 核对新启动时间） |
| **后端重启**（改 `indicator_groups.py`/`imaging_items.py` 等"读取时计算"逻辑后必做） | `bash /tmp/restart_backend.sh`（内容见附录 A） |
| **OCR（扫描件必看）** | 8001 PaddleOCR-VL；**长时间运行 `/ocr` 会 500（`int(Tensor) not supported in static graph mode`），实测每 15-70 页触发，重启即恢复**；重跑扫描件前先 curl 验一页 |
| OCR 看门狗 | `app/core/ocr_watchdog.py`（随 8000 startup）：深度探活(真实小图)/连续 2 次失败自动重启/空闲窗口滚动重启(50 页或 4h)；日志 `app.ocr.watchdog` |
| OCR 页级缓存 | `OCR_CACHE_DIR=/tmp/ocr_page_cache`（内容寻址 sha1(图片)）；崩溃重试只补未缓存页。默认关，批量重跑建议开 |
| 强制 OCR | `FORCE_OCR_TASKS=<task_id,...>`（`backend/.env` 或环境变量）；文本层字体映射乱码件（如 89 陈宏宏）用 |
| DB | `docker exec hospital-mysql mysql -uroot -proot --default-character-set=utf8mb4 ...`；租户库 `hospital_H001..H004`/`hospital_1`；**跨库多表 DELETE 需 `USE db;`**（否则 1046） |
| 样本库 | 护栏引用 `/home/wjyy2/hospitalKnowledgeBase/体检报告样例/`（旧 checkout）+ 本仓库 `体检报告样例/0-第三批|0-第四批|0-第五批|0-第6批|0-第7批|0-第8批` |
| venv | `backend/.venv`（主，cu126 锁定）/ `.venv-vllm-cu12`（仅 vLLM）/ `paddle_venv`（OCR） |
| 离线仿真（秒级） | `scripts/offline_indicator_assemble.py <pdf> [--hybrid] [--force-ocr] [--text-file F]`；`scripts/offline_conclusion_assemble.py <pdf> --report-id N --db H00X [--sync] [--hybrid]` |
| LLM 快照重放 | `ABNORMALITY_LLM_SNAPSHOT_SAVE=1 ABNORMALITY_LLM_SNAPSHOT_DIR=backend/artifacts/llm_snapshots` 真调一次；之后同 DIR 重放（命中日志 `abnormality snapshot replay`） |
| 质量扫描 | `scripts/scan_report_quality.py <pdf|目录>`（无 LLM 五类可疑判定） |
| 端到端重跑（**用户发令后**） | `OCR_BASE_URL=http://localhost:8001 OCR_CACHE_DIR=/tmp/ocr_page_cache BULK_WINDOW_START=0 BULK_WINDOW_END=24 .venv/bin/python scripts/e2e_rerun_reports.py --db hospital_H004 --reports N,M [--mode reprocess|reinterpret]` |
| 单测（纯函数，秒级） | `pytest tests/modules/report/test_extraction_units.py -q` |

---

## 3. 标准流程 v2（7 步，摘要）

1. **样本入库 + 形态体检**：PDF 放 `体检报告样例/<批次>/`；`conclusion_samples.py` 登记
   （精修/`smoke=True`/`ocr=True`）；fitz 看页数/文本/图片 → 文本 / 纯扫描 / 文本层损坏（force-ocr）/
   多栏（visual_sort）/ 混合图片结论页（ocr_content_pages）→ 决定链与 OCR。
2. **先补保护再改规则**：最小复现片段补 `test_extraction_units.py`（含"不得多产"断言）；
   整份入 `conclusion_samples.py`。
3. **离线仿真 + 快照重放迭代**：差异**数据化**进 `report_profiles.py`；禁止按医院名写运行时分支。
4. **跑全量护栏**确认旧报告零漂移；与旧验收冲突 → 说明影响面交用户决定，不私自改旧基线。
5. **端到端一次（用户发令）+ 用户前端验收**：字段（姓名/性别/年龄/医院/编码/年份）、
   指标黄红名单、结论条目、计数（红/黄/绿 = 指标逐条 + 总检建议整体 +1）。
6. **冻结三层护栏**（§4）→ 提交 + 更新 AGENTS.md。
7. **回滚点**：改动前 tag/commit；回滚判据 = 护栏样本非预期缺失、或新报告垃圾条目 >3。

**档案开关（`report_profiles.py`，新增必须登记 `match_profile` 白名单）**：
`visual_sort` / `visual_lines` / `extra_anchor|skip|break` / `anchor_only` / `keep_post_sig` /
`keep_normal_numbered` / `keep_bare_numbers` / `table_conclusion` / `review_block` /
`multi_findings` / `ocr_content_pages` / `red_marks` / `red_marks_ref` / `summary_marks` /
`latin_wrap` / `reversed_dump` / `no_colon_strip` / `despace` / `tail_anchor|tail_before`。

---

## 4. 三层护栏登记位置（冻结时改这些）

| 层 | 文件 | 说明 |
|---|---|---|
| DB 三件套 | `scripts/gen_h003_baseline.py` / `gen_h004_baseline.py` 的 `REPORT_IDS` → 重跑 gen → `baselines/h00X_baseline.json` | 追加新 id 后**旧条目必须零 diff**（`git diff` 审 0 deletions）；`test_h00X_baseline_guard.py` 按 JSON keys 自动参数化 |
| 结论重提取 | `test_conclusion_text_reguard.py` 的 `CASES` + `MATCH`（严格 hash）/`HYBRID`（离线锁） | 文本件→尝试 MATCH；扫描/图片/无结论段→HYBRID。**`_re_extract_text` 必须与生产同构**（现含 `keep_post_sig`/`keep_normal_numbered`/`keep_bare_numbers`）；批次目录常量分开（B3…B8） |
| 指标重提取 | `test_gx_bj_indicator_guard.py::_BASELINE_CASES` | 仅文本可复现；纯扫描/force-ocr 不纳入。**`_extract` 必须与生产同构**（red_marks/red_marks_ref/summary_marks/latin_wrap/跨通道合并/范围单位折行） |
| 样本库 | `conclusion_samples.py` | 新批次整份登记（rel 以文件实际名为准；注意 NBSP） |

**本轮新增的同构点（已同步）**：`scripts/offline_indicator_assemble.py`、`test_gx_bj_indicator_guard._extract`
都包含：redref 标记、summary_marks、latin_wrap、列式/行式 ref-unit 合并、行式 result+unit==列式 result 去重、
`_merge_range_fragments`、`_QUAL_ANNOT_RE`（阴性Hp(-)）。改生产链时**三处一起改**，否则护栏假红/假绿。

---

## 5. 验证纪律（硬约束）

- **分层验证**：中间迭代 = 离线仿真 + 秒级纯函数断言；**端到端只在用户发令后**。
- **护栏盲区**（验不出）：junk 滤卡 / fill / 展开 / 跨线去重 / 新字段 / 扫描 VLM 路径 ——
  靠纯函数断言 + DB 快照 + 离线仿真兜底。
- **LLM 侧节奏**：真调一次取快照 → 快照重放迭代 → 收敛后真调一次 + 端到端一次。
- **护栏触发规则**：触碰回归集覆盖的解析层 → 提醒用户跑全量，跑不跑由用户定；
  junk/fill 等盲区层不提醒也不自动跑。
- **不得手动改 DB 当验收**；手动 SQL 仅一次性清理，且之后端到端复验。
- **改代码后必须重启相应进程**（worker / 后端 / OCR）并核对 `ps` 启动时间。
- 汇报格式：`医院(姓名)`；每个修复附离线/端到端证据。

---

## 6. 已知坑与本轮教训（避免返工）

1. **OCR 崩溃有固定节奏**：`int(Tensor)` 约每 15-70 页一次；看门狗滚动重启阈值已降到 50 页。
   进程内重建 pipeline **无效**（试过已回退），只能进程级重启。扫描件重跑前先 curl 验一页。
2. **hybrid 页级 OCR 阈值**：文本 >100 字且图片页不会被 OCR —— 结论跨页续段可能整块丢失
   （93 陈行富第 7 页 135 字 + 图片结论块）。解：档案 `ocr_content_pages=True` + `hybrid, ocr_content=True`。
   注意：**原文层已是完整单元格转储的页不要 OCR**（会丢单位列，`_text_looks_like_cell_dump` 守着）。
3. **`match_profile` 白名单**：新开关不登记 = 形同虚设（本轮 `keep_bare_numbers` 曾漏登记，
   97 序号整轮没生效）。目前白名单：anchor_only/visual_sort/table_conclusion/review_block/
   multi_findings/visual_lines/keep_post_sig/keep_normal_numbered/keep_bare_numbers/
   ocr_content_pages/despace/no_colon_strip/red_marks/red_marks_ref/summary_marks/latin_wrap。
4. **「检测值|结果」双值表列语义**（97 开滦，用户确认）：`检测值`=主结果（2+/阴性Hp(-)/25），
   `结果`列=计数值/报告方标注（50）→ `flag2`，任何非空=异常记号判黄。**不要把所有表都改列语义**；
   跨通道去重：行式 `result+unit` == 列式 `result` 时保留列式（`电导率信息 3 级`）。
5. **同名跨通道**：列式 ref 截断（高位≤低位或行式高位更完整）→ 取行式完整 ref；列式单位是行式单位
   前缀（`mmol/` vs `mmol/L`）→ 取完整单位。
6. **范围/单位折行**：`_merge_range_fragments`（下一行 1-4 位纯数字 + 小数位不齐/高位<低位 → 拼接），
   例：`0.108-0.2+82`、`1.003-1.0+30`、`5000-1200+0`。定性值带拉丁标注（`阴性Hp(-)`）必须算值，
   否则会被当名称产重复行（`阴性Hp(-)|14`）。
7. **结论 reflow**：编号条目“序号+异常名”独立成行、正文另起（`prev=="number"` flush + `in_num_body`
   抑制正文内“短标题→长解释”拆行）；句中折行（`也可/见于`、`间质/性肾炎`、`桥本甲/状腺炎`）靠
   `_is_plain_finding_boundary` 的 `_single`/`pf_short(≤20 字)` 约束；改这些会动冻结结论 hash。
8. **总结段标黄（`summary_marks`，日照市中医）**：只标**既有**指标行、每个【异常项】只标最长匹配行；
   结果 `"-"` 时其后的“弱阳性”是参考范围列，不是标志（尿胆原，96 同误黄已清）。
9. **红字语义按院**：恩施 `red_marks`（红字=异常，强制黄）；望京 `red_marks_ref`（红字+**有参考**→黄，
   无参考红字保持绿；PH 5 打印参考 4.6-8.0 仍判黄、AST 13 同判）。
10. **姓名栏标签/值分列**：文本流取不到姓名值时用 `extract_name_from_layout`（首页“姓 名:”同排右侧
    首格）→ 陈行富；文本启发式（尊敬的/所有姓名标签/坏名消除交 LLM）仍作兜底。
11. **读取层分类（改完必须重启后端）**：裸 `PH`/`尿PH`→尿常规；同名“葡萄糖”非数值→尿常规、
    数值(mmol/L)→空腹血糖；隐血/潜血→尿常规；裸“蛋白”→尿常规；白带上下文规则见 AGENTS.md。
12. **e2e 工具**（本轮已加固）：reprocess **不再重复投递** interpretation（process_task 自带）；
    OCR 故障自动等恢复后重跑（≤2 次）；单份失败记录后继续；wipe 会清 `conclusion_text`/`parsed_name`。
13. **护栏同构**：任何生产链改动必须同步 `offline_indicator_assemble.py`、`test_gx_bj_indicator_guard._extract`
    与 `test_conclusion_text_reguard._re_extract_text`（本轮 `keep_bare_numbers` 曾只在生产生效）。
14. **基线重生成**：`gen_h004_baseline.py` 追加 REPORT_IDS → 重跑 → `git diff baselines/*.json` 人工审
    （必须只有新增条目）；旧条目任何变化都要人工确认原因。
15. **提交约定**：只提交后端 + `AGENTS.md`/`docs`；前端 3 个文件 + 2 张 jpg 永不提交；
    提交信息由用户给（照抄，不自行改写）。
16. **93/96 的 HYBRID 原因**：93 扫描混合件（生产 `ocr_content_pages` OCR）；96 无结论段样本
    （生产 conclusion_text 为 NULL）—— 离线锁只防漂移，生产文本由 DB 三件套守。

---

## 7. 下一批入口动作（建议顺序）

1. 向用户确认：样本路径/份数/是否有扫描件；上传到哪个租户（H003/H004 或新租户——新租户需按
   AGENTS.md“新 tenant 初始化必读”建全部表，含 followup/batch_import 等）。
2. 形态体检：逐份 fitz（页数/文本/图片）+ OCR 健康（curl 一页）+ `scan_report_quality.py`。
3. 登记样本 + 最小复现断言（§3 第 1-2 步）。
4. 逐份离线仿真 → 问题清单 → 修复（数据化）→ 全量护栏 → 汇报。
5. 用户验收 → 冻结（§4）→ 提交 + AGENTS.md。

---

## 附录 A：重启脚本（/tmp 清空后按此重建）

**`/tmp/restart_workers2.sh`**（只杀本 checkout worker，WORKER_TAG 精确匹配）：

```bash
#!/bin/bash
BACKEND_DIR=/data/project/hospitalKnowledgeBase/backend
export BULK_WINDOW_START=0 BULK_WINDOW_END=24 LOG_LEVEL=INFO
pkill -f "start_worker.*# $BACKEND_DIR" 2>/dev/null
sleep 3
cd "$BACKEND_DIR"
for i in 1 2; do
  setsid nohup .venv/bin/python -u -c "from app.modules.report.worker import start_worker; start_worker() # $BACKEND_DIR" \
    > /data/logs/worker-parsing.$i.stdout.log 2>&1 < /dev/null &
  echo $! > /tmp/start-sh-worker-parsing.$i.pid
done
for i in 1 2 3; do
  setsid nohup .venv/bin/python -u -c "from app.modules.interpretation.worker import start_worker; start_worker() # $BACKEND_DIR" \
    > /data/logs/worker-interpretation.$i.stdout.log 2>&1 < /dev/null &
  echo $! > /tmp/start-sh-worker-interpretation.$i.pid
done
setsid nohup .venv/bin/python -u -c "from app.modules.report.extract_worker import start_worker; start_worker() # $BACKEND_DIR" \
  > /data/logs/worker-extract.1.stdout.log 2>&1 < /dev/null &
echo $! > /tmp/start-sh-worker-extract.1.pid
echo "restart2 done"
```

**`/tmp/restart_backend.sh`**：

```bash
#!/bin/bash
BACKEND_DIR=/data/project/hospitalKnowledgeBase/backend
cd "$BACKEND_DIR"
pkill -f "uvicorn app.main:app --host 0.0.0.0 --port 8000" 2>/dev/null
sleep 3
setsid nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  > /data/logs/backend.stdout.log 2>&1 < /dev/null &
echo "backend relaunch pid $!"
```

> 注意：仓库内 `scripts/start_workers.sh` 是**旧 checkout**（/home/wjyy2、8005/8006、
> vhost hospital_dev_wjyy2）的脚本，**不要用**。

## 附录 B：第八批（93-98）报告 ↔ id / 样本

| id | 姓名 | 医院 | 样本 | 冻结层 |
|---|---|---|---|---|
| 93 | 陈行富 | 厦门大学附属心血管病医院 | `0-第8批/陈行富284014.pdf` | DB + 结论 HYBRID（扫描混合，ocr_content） |
| 94 | 陈阳 | 中国中医科学院望京医院 | `0-第8批/陈阳144428.pdf` | DB + 结论 MATCH + 指标重提取（red_marks_ref） |
| 95 | 封猛 | 日照市中医医院 | `0-第8批/封猛196030.pdf` | DB + 结论 MATCH + 指标重提取（summary_marks） |
| 96 | 付本明 | 日照市中医医院 | `0-第8批/付本明160910无结论段.pdf` | DB + 结论 HYBRID（无结论段）+ 指标重提取 |
| 97 | 韩先锋 | 开滦总医院 | `0-第8批/韩先锋_6762853_5.pdf` | DB + 结论 MATCH + 指标重提取（latin_wrap/keep_bare_numbers/双值表） |
| 98 | 李承龙 | 临沂市中医医院 | `0-第8批/李承龙291816.pdf` | DB + 结论 MATCH + 指标重提取（历次对比跨页屏蔽） |

> 全量 88 份 id 映射见 AGENTS.md 各批次冻结小节；本表仅列第八批后 6 份。
> 第八批前 5 份：88 包强/89 陈宏宏（FORCE_OCR）/90 陈佳漩/91 陈坤铭/92 陈婷婷（见 AGENTS.md）。
