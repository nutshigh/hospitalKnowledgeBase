# AGENTS.md —— 给后续 AI Agent 的工程记忆

本文件记录代码里不容易直接看出的工程决策与环境约束,供后续接手的 Agent 快速理解。**修改本文件前请确认事实,不要凭推测改写。**

---

## vLLM 不在 `backend/pyproject.toml` 的依赖里 (重要)

**事实**: `start.sh` 用 `backend/.venv-vllm-cu12/bin/vllm` 启动 MedGo / BGE-M3 两个推理服务,该 venv 是**手工独立维护**的,**不进 `uv.lock`**。

**原因**:
- vLLM 在本架构里是 `start.sh` 拉起的外部 HTTP 服务,backend 业务代码 **没有任何 `import vllm`**(已核实 `app/`、`reranker_service/` 下均无)。
- 主 venv (`backend/.venv`) 因驱动 535 / CUDA 12.2 限制只能跑 cu12(torch 2.7+cu126);而 vLLM 在 backend 的 `requirements` 中默认会被 uv 解到 cu13 + vllm 0.23,与驱动不兼容,启动即崩。
- 所以把 vllm 从 backend 依赖里移除,改由独立 venv 提供;主 venv 仍用 cu12 跑 Backend / Reranker / Workers。

**不要做的事**:
- ❌ 不要往 `backend/pyproject.toml` 里重新加 `"vllm>=..."`
- ❌ 不要 `cd backend && uv sync` 期望它装出能跑 vllm 的环境 —— 它故意不装 vllm
- ❌ 不要把 `.venv-vllm-cu12/` 删了重建为最新版 vllm(0.22+)—— 那会拉到 cu13 / torch 2.11,与驱动 535 不兼容

**重建 `.venv-vllm-cu12` 的方法(如丢失)**:
```bash
cd backend
uv venv .venv-vllm-cu12 --python 3.10
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv pip install \
  --python .venv-vllm-cu12/bin/python \
  'vllm==0.9.2' 'transformers==4.51.3' 'tokenizers==0.21.4'
```
注意 transformers 必须 pin 4.51.3 —— vllm 0.9.2 与 transformers 5.x 冲突(`aimv2` 注册重复 + `prepare_for_model` 被移除,FlagEmbedding 还在用)。

**核对 venv 健康**:
```bash
backend/.venv-vllm-cu12/bin/python -c "import torch,vllm;print(torch.__version__,vllm.__version__,torch.cuda.is_available())"
# 期望: 2.7.0+cu126 0.9.2 True
```

---

## 主 venv (`backend/.venv`) 的 cu12 锁定

`backend/pyproject.toml` 末尾有:
```toml
[tool.uv]
torch-backend = "cu126"
```
且 `dependencies` 中显式 pin:
- `torch==2.7.0` —— 否则 uv 会拉 torch 2.11+cu13,在本机驱动 535 上 import 即崩
- `transformers==4.51.3` —— 配合 FlagEmbedding 1.4(transformers 5.x 移除了 `prepare_for_model`)
- `tokenizers==0.21.4` —— transformers 4.51 配套

**改这些 pin 前请确认驱动支持**:当前 nvidia 驱动 535.247.01 / CUDA 12.2,Ubuntu 20.04 apt 源顶天到 575(无 580+),所以 cu13 路径在本机走不通。要升驱动只能先升 OS(不在本项目范围)。

---

## start.sh GPU 分配 (4×L20, 每卡 45GB)

| GPU | 服务 | 显存占用 |
|-----|------|---------|
| 0,1,2,3 | MedGo vLLM (TP=4, 32K ctx, util 0.6, enforce-eager) | ~27.8GB/卡 |
| 2 | BGE-M3 vLLM (util 0.12) + Reranker (主venv) | ~angoing |
| 3 | PaddleOCR-VL (paddle_venv, 独立) | 较小 |

`enforce-eager` 关闭 CUDA 图,降低显存碎片,利于 4 卡共存场景。`--gpu-memory-utilization 0.6` 给 MedGo 是为给同卡上的 BGE/Reranker/OCR 让出空间。

**3 个 venv 关系**:
- `backend/.venv` —— 主 FastAPI 后端 / Reranker / Workers (uv 管理, cu12)
- `backend/.venv-vllm-cu12` —— 仅供 start.sh 拉 MedGo/BGE (手工, vllm 0.9.2+cu126)
- `backend/paddle_venv` —— PaddleOCR-VL 专属 (uv 管理)

---

## 验证一切就绪

```bash
# 全套健康
for p in 8000 8004 8002 8003 8001; do curl -s -m2 http://localhost:$p/health >/dev/null && echo ":$p UP" || echo ":$p DOWN"; done

# MedGo 推理(TP=4)
curl -s http://localhost:8004/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"/data/models/MedGo","messages":[{"role":"user","content":"hello"}],"max_tokens":10}'

# BGE-M3 Embedding
curl -s http://localhost:8002/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"BAAI/bge-m3","input":"test"}'

# Reranker
curl -s http://localhost:8003/rerank -H 'Content-Type: application/json' \
  -d '{"query":"糖尿病","documents":["血糖","天文"],"top_n":2}'
```

冷启动 `bash start.sh` 在 ~90 秒内能完成全部服务启动(已验证)。

---

## 备份 (按需清理)

- `backend/.venv.bak-cu13-*` —— 原 cu13 损坏态历史快照,可删
- `backend/.venv.bak-pre-cu12fix-*` —— cu12 化前快照,可删
- `backend/pyproject.toml.bak-*` / `backend/uv.lock.bak-*` —— 文本回滚备份,可删
- `/tmp/nvidia-snap-latest.txt` —— 驱动包列表只读快照,可删

git 已跟踪改动可直接 `git checkout -- start.sh backend/pyproject.toml backend/uv.lock` 回滚;`.venv-vllm-cu12` 是新目录不在 git 内,如需彻底回滚需手动删。

---

## 新 tenant 初始化必读

`start.sh` 的数据库初始化 DDL 块只对 `hospital_H001` 跑一次(`CREATE TABLE IF NOT EXISTS`)。**新增 tenant 时必须照此 DDL 块为新 tenant 的库完整执行一遍**,否则该 tenant 缺表会直接报错。完整表清单(逐表对应 `start.sh` 内的 `CREATE TABLE IF NOT EXISTS`):

| 旧业务表 | 用途 |
|------|------|
| `hospital_user` | 医院用户档案 |
| `knowledge_category` / `knowledge_entry` | 知识库分类与条目 |
| `report_task` / `report_info` / `report_indicator` | 体检报告解析 |
| `report_interpretation` / `indicator_judgment` | AI 解读与指标判定 |
| `triage_rule` | 分诊规则 |
| `report_template` / `statistic_cache` / `dispatch_config` / `resource_metric` | 模板/统计缓存/分诊配置/资源监控 |
| `chat_session` / `chat_message` | 聊天会话 |

| 批量上传新增表(易遗漏) | 用途 |
|------|------|
| `batch_import` | 批量上传批次 |
| `batch_import_file` | 批次内单文件(含 `failed_stage` 列,记录失败阶段) |

`batch_import_file.failed_stage` 是增量列,旧库需 `ALTER TABLE batch_import_file ADD COLUMN IF NOT EXISTS failed_stage VARCHAR(24) DEFAULT NULL`(`start.sh` 已带,新 tenant 建表时直接包含)。

`failed_stage` 已知取值:`parsing` / `interpretation` / `oversize` / `dispatch_unmatched`。
- `oversize`:单文件 > 50MB,无 `report_task_id`,**不可重试**(UI 禁用重试按钮)。
- `dispatch_unmatched`:批量上传时文件名不符合 `<姓名>_<医院编号>_<用户编号>.<ext>` 约定(三段下划线、末段纯数字),不 create_task 不投 parsing。**不可重试**,需 admin 改文件名后整批重新上传。
- 后端 `retry_failed` 把这两类统称 unretryable,在响应里以 `skipped_unretryable` 计数返回,不重投。