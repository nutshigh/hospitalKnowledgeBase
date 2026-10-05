# 第五批报告适配 · 交接文档（给下一个 Agent）

> 本仓库：`/data/project/hospitalKnowledgeBase`（主 checkout，后端 8000）
> 生成时间：2026-09-25（第四批 10 份验收+冻结完成后）
> **动手前必读 `AGENTS.md` 的相关章节**（本文件是它的浓缩入口，冲突以 AGENTS.md 为准）

---

## 0. 你的任务

适配**第五批报告**（样本路径待用户确认，预计 `体检报告样例/0-第五批/`），
按《新地区报告适配标准流程 v2》（AGENTS.md 同名章节）执行，产出：
①解析规则修复（数据化进 `report_profiles.py`）②新报告纳入三层基线护栏 ③AGENTS.md 记录 ④提交。

**用户协作方式**：用户会在前端逐份验收并给出具体问题清单（格式：`医院报告：1.… 2.…`）。
你负责：离线复现 → 修规则 → 跑全量护栏 → 汇报（含证据）→ 用户验收 → 冻结基线。
**端到端重跑（reprocess/reinterpret）只在用户明确发令后执行。**

---

## 1. 当前状态（第四批收口后）

| 项 | 现状 |
|---|---|
| 已适配并冻结的报告 | **46 份**：H003 基线 18 份 + H004 基线 28 份（`tests/modules/report/baselines/`） |
| 全量护栏 | `cd backend && .venv/bin/python -m pytest tests/modules/report/ tests/test_safety_net.py -q` → **560 passed / 29 skipped** |
| 结论重提取护栏 | 31 MATCH（严格 hash==基线）+ 15 离线锁（扫描件，锁空产物防漂移） |
| 样本库 | `conclusion_samples.py` 45 条（精修强断言 / smoke 冒烟 / ocr 需 OCR 默认跳过） |
| 已适配报告 ↔ id 映射 | 见附录 B |

**已知遗留（不属第五批，可顺带）**：
1. `unit_name=医院（体检机构）` + `exam_no`（体检编码）+ `exam_year`（仅年份）落库方案
   （口径已定，**未实施**：需 DDL 三处同步 + 提取器 + 回填 + 测试，见 AGENTS.md 历史讨论）；
2. 45 方轶涛/49 曹剑鸿 结论条目偏少（LLM 抽取，用户已验收）；
3. 前端 2 个文件未提交（参考范围符号重复修复：`user-portal/IndicatorRow.tsx`、
   `doctor-portal/ReportDetailPage.tsx`）——用户要求"非后端功能文件不提交"。

---

## 2. 环境与工具速查（本 checkout 口径）

| 事项 | 命令/说明 |
|---|---|
| 服务启停 | `bash start.sh`（启动即返回）/ `bash start.sh --stop`（Docker 中间件不动） |
| 端口 | 后端 **8000**、embed 8002、reranker 8003、MedGo 8004、**OCR 8001**、前端 3001/3002/3003 |
| **worker 重启**（改 worker 链代码后必做） | `/tmp/restart_workers2.sh`（内容见附录 A；跑完 `ps` 核对新启动时间） |
| **后端重启**（改 `indicator_groups.py` 等"读取时计算"逻辑后必做） | `cd backend && pkill -f "uvicorn app.main:app"; setsid nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /data/logs/backend.stdout.log 2>&1 < /dev/null &` |
| **OCR（扫描件必看）** | 8001 PaddleOCR-VL；**长时间运行 `/ocr` 会 500（int(Tensor)），重启即恢复**；重跑扫描件前先 curl 验一页（命令见 AGENTS.md"OCR 服务端口与本 checkout 口径"） |
| DB | `docker exec hospital-mysql mysql -uroot -proot --default-character-set=utf8mb4 ...`；租户库 `hospital_H001..H004`/`hospital_1`；**跨库多表 DELETE 需 `USE db;`**（否则 1046） |
| 样本库 | 护栏引用 `/home/wjyy2/hospitalKnowledgeBase/体检报告样例/`（旧 checkout）；第三/四批在本仓库 `体检报告样例/0-第三批|0-第四批` |
| venv | `backend/.venv`（主，cu126 锁定）/ `.venv-vllm-cu12`（仅 vLLM）/ `paddle_venv`（OCR） |
| 离线仿真（秒级） | `scripts/offline_indicator_assemble.py <pdf>`；`scripts/offline_conclusion_assemble.py <pdf> --report-id N --db H00X [--sync] [--hybrid]` |
| LLM 快照重放 | `ABNORMALITY_LLM_SNAPSHOT_SAVE=1 ABNORMALITY_LLM_SNAPSHOT_DIR=backend/artifacts/llm_snapshots` 真调一次；之后同 DIR 重放（命中日志 `abnormality snapshot replay`） |
| 质量扫描 | `scripts/scan_report_quality.py <pdf|目录>`（无 LLM 五类可疑判定） |
| 端到端重跑（**用户发令后**） | `OCR_BASE_URL=http://localhost:8001 BULK_WINDOW_START=0 BULK_WINDOW_END=24 .venv/bin/python scripts/e2e_rerun_reports.py --db hospital_H004 --reports N,M [--mode reprocess|reinterpret]` |
| 单测（纯函数，秒级） | `pytest tests/modules/report/test_extraction_units.py -q` |

---

## 3. 标准流程 v2（7 步）

1. **样本入库 + 形态体检**
   - PDF 放 `体检报告样例/<批次>/`；`conclusion_samples.py` 登记（精修/`smoke=True`/`ocr=True`）
   - 形态体检：逐份看每页文本/图片数（fitz）→ 文本 / 纯扫描 / 文本层损坏（force-ocr）/ 多栏（visual_sort）
     → 决定走哪条链、要不要 OCR；`scan_report_quality.py` 兜底
2. **先补保护再改规则**：最小复现片段补 `test_extraction_units.py`（**含"不得多产"断言**）；
   整份入 `conclusion_samples.py` —— 否则迭代期新报告无护栏
3. **离线仿真 + 快照重放迭代**：秒级验证；差异**数据化**进 `report_profiles.py`
   （`visual_sort`/`visual_lines`/`extra_anchor/skip/break`/`anchor_only`/`keep_post_sig`/
   `keep_normal_numbered`/`table_conclusion`/`review_block`/`multi_findings`）——**禁止按医院名写运行时分支**
4. **跑全量护栏**（§1 命令）确认旧报告零漂移；与旧验收冲突 → **说明影响面交用户决定**，不私自改旧基线
5. **端到端一次（用户发令）+ 用户前端验收**：验收清单 = 字段（姓名/性别/年龄/医院/编码/年份）、
   指标黄红名单、结论条目、计数（红/黄/绿 = 指标逐条 + 总检建议整体 +1）
6. **冻结入三层护栏**（见 §4）→ 提交 + 写 AGENTS.md
7. **回滚点**：改动前 tag/commit；回滚判据 = 护栏样本非预期缺失、或新报告垃圾条目 >3

---

## 4. 三层护栏登记位置（冻结时改这些）

| 层 | 文件 | 说明 |
|---|---|---|
| DB 三件套 | `scripts/gen_h003_baseline.py` / `gen_h004_baseline.py` 的 `REPORT_IDS` → 重跑 gen → `tests/modules/report/baselines/h00X_baseline.json` | `test_h00X_baseline_guard.py` 按 JSON keys 自动参数化；旧条目必须零变化 |
| 结论重提取 | `test_conclusion_text_reguard.py` 的 `CASES` + `MATCH`（严格 hash）/`HYBRID`（离线锁 `da39a3ee5e6b4b0d`） | 文本件→MATCH；扫描/图片件→HYBRID；**第四批目录常量 `B4` 与第三批 `B3` 分开**（曾误用导致静默跳过） |
| 指标重提取 | `test_gx_bj_indicator_guard.py::_BASELINE_CASES` | 仅**文本可复现**的报告；纯扫描不纳入（规则链无产物） |
| 样本库 | `conclusion_samples.py` | 新批次整份登记 |

---

## 5. 验证纪律（硬约束）

- **分层验证**：中间迭代 = 离线仿真 + 秒级纯函数断言；**端到端只在用户发令后**
- **护栏盲区**（验不出，靠纯函数断言 + DB 快照 + 离线仿真）：junk 滤卡 / fill / 展开 /
  跨线去重 / 新字段 / 扫描 VLM 路径
- **LLM 侧节奏**：真调一次取快照 → 快照重放迭代 → 收敛后真调一次 + 端到端一次
- **护栏触发规则**：触碰回归集覆盖的解析层（切段/锚点/断点/reflow/visual/标题解析/判定层/归一）
  → **提醒用户跑全量**，跑不跑由用户定；其它层（junk/fill 等）不提醒也不自动跑
- **不得手动改 DB 当验收**；手动 SQL 仅一次性清理，且之后端到端复验
- **改代码后必须重启相应进程**（worker / 后端 / OCR）并核对 `ps` 启动时间
- 汇报格式：`医院(姓名)`；每个修复附离线证据

---

## 6. 已知坑与近期教训（避免返工）

1. **OCR 8001 崩溃**：`int(Tensor) not supported in static graph mode` → **重启即恢复**；
   扫描件首轮"空产物/空指标"多半是它（52/55/56 真实故障）
2. **VLM 表格解析已重构**（勿回退）：逐 `<table>` 块解析；单表双栏按表头角色最小周期拆子行；
   历年对比表（表头 ≥2 日期列）整表跳过；标志格归一（`$ \uparrow $`→↑）；结果右侧尾箭头先剥 `$`；
   标志扫描**限定已映射列**（历史列"阳性"不是标志）；"本次小结"行兜底补 f=3；
   数值越界兜底**只在 ref 拆分后**执行（拆分前 `_as_float("<2.7")` 会把单侧上限当下限）；
   分级定性参考（"阴性:<0.9 可疑…阳性…"）+ 数值落阴性带 → 清误读标志（用户口径：保留行）
3. **`match_profile` 白名单**：新档案开关必须登记进合并白名单，否则档案声明不生效（曾踩）
4. **分类读取时计算**：`indicator_groups.classify` 改动**必须重启后端**才生效（前端看不到变化先查这个）；
   微白蛋白→尿常规；尿微量白蛋白/转铁蛋白/免疫球蛋白G/α1微球蛋白/甲状腺球蛋白抗体→免疫检测
5. **别名不合并**："微白蛋白"与"尿微量白蛋白"是**两项不同指标**（曾错误合并，已回滚）
6. **换行/合并形态由用户前端习惯决定**：`merge_wrapped` 曾加又被用户要求回滚 —— 不要按"片段合并"直觉改
7. **个人信息**：`_name_ok`（机构/日期/地址词不算姓名）+ 扫描所有"姓名"标签取首个合理值 +
   "尊敬的XXX先生/女士"兜底 + 坏名清除交 LLM；年龄 1-120 校验 + 缺年龄触发 LLM 兜底
8. **结论侧档案开关**：`visual_sort`（块级坐标）/`visual_lines`（行级，两栏结论配对）/
   `keep_post_sig`（签名后强标题段）/`keep_normal_numbered`（编号正常行不算噪声）/`anchor_only`
9. **`_norm_anchor` 已剥 markdown `#`**（OCR 标题带 `##`，档案锚点曾因此失配）
10. **孤儿判定**：rerun wipe 竞态会遗留指向已删 interpretation 的 judgment（UI 不可见）；
    清理语句须 `USE db;`（2026-09-25 已清 H001/H003/H004 共 9312 条）
11. **e2e 预检**：`e2e_rerun_reports.py` reprocess 模式会先验原文存在，缺失则跳过不 wipe
12. **样本文件名**：第三批陈磊的文件名含 **NBSP**（`陈磊27441X\u00a0.pdf`）；第四批华裕健曾改名
    （`61581770.pdf`→`华裕健062315.pdf`）—— 以 `conclusion_samples.py` 的 `rel` 为准
13. **worker 重启**：改 worker 链代码（service.py/table_extractor.py 等）后必须重启并核对时间，
    否则"改了没用"；`/tmp/restart_workers2.sh` 只杀本 checkout 的 worker（WORKER_TAG）
14. **提交约定**：非后端功能文件（前端等）不提交；提交信息用户会给（按其原样）

---

## 7. 第五批入口动作（第一轮建议顺序）

1. 向用户确认：样本路径/份数/是否有扫描件；报告将上传到哪个租户（H003/H004 或新租户——新租户需按
   AGENTS.md"新 tenant 初始化必读"建全部表）
2. 形态体检：逐份 fitz（页数/文本/图片）+ OCR 健康（curl 一页）+ `scan_report_quality.py`
3. 登记样本 + 最小复现断言（§3 第 1-2 步）
4. 逐份离线仿真 → 问题清单 → 修复（数据化）→ 全量护栏 → 汇报
5. 用户验收 → 冻结（§4）→ 提交 + AGENTS.md

---

## 附录 A：`/tmp/restart_workers2.sh`（/tmp 清空后按此重建）

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

> 注意：仓库内 `scripts/start_workers.sh` 是**旧 checkout**（/home/wjyy2、8005/8006、
> vhost hospital_dev_wjyy2）的脚本，**不要用**。

## 附录 B：已适配报告 ↔ 报告 id 映射（46 份）

| 批次 | H003 | H004 |
|---|---|---|
| 广西 13 | 1 崇左(滕寿权)/2 广西人民(朱品龙)/3 百色(刘丹)/4 贵港(尹剑鸣)/5 防城港中(胡鹏)/6 防城港一(徐伟祥)/7 陈美杉 | 1 柳州(石坤)/2 桂林(张亚)/3 梧州(谢国宾)/5 钦州二(庞海锋)/6 步新宇 |
| 各地汇总 13 | 20 德宏(毕建国)/22 滨州(董延广)/23 潮州(陈桂深)/24 福建第二(谭建平)/25 马鞍山(陈田)/26 池州(曹敬金) | 21 华西(林建生)/22 弘爱(戴伟平)/23 山东省立(王国瑞)/24 日照(邵琳)/25 茂名(陈灿明)/26 莆田(蔡芳坤)/27 齐鲁(李林青) |
| 第三批 10 | 27 鲍文祥/28 常逢龙/29 包雁飞/30 陈磊/31 曹嘉冰 | 38 安鹏/39 蔡超/40 陈镜霓/41 白玮衡/43 高帅 |
| 第四批 10 | — | 45 方轶涛/46 程永春/47 陈诚/48 华裕健/49 曹剑鸿（文本）；50 薄楠/51 鲍海宽/52 曾社健/55 陈淳铭/56 陈海波（扫描） |

> 说明：45 与 29 是"线上记录 id"（重复批次记录已清理：H004-4 欧阳庆批次、30 曹敬金、32/33/34 董延广）。
