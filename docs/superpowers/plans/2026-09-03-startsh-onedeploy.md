# start.sh 一键部署改造(启动即返回 + 独立 --stop) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `start.sh`/`start_front.sh` 从「前台阻塞(wait) + Ctrl+C 全杀(trap cleanup)」改成「启动即返回 shell + 独立 `--stop` 停应用层服务」,消除"任何 SIGTERM = 全杀"的核弹隐患。

**Architecture:** 三个最小改动点:① 参数解析加 `--stop` 标记 → 在 cleanup 定义后显式调用 `cleanup()`(内含 exit 0);② 删除 `trap cleanup SIGINT SIGTERM`(启动路径不再被外部信号全杀);③ 删除末尾 `wait`(启动即返回)。服务均为 `nohup ... > log 2>&1 &`,脚本退出后子进程脱离继续运行(已验证)。启动/停止幂等由现有守卫保障。

**Tech Stack:** Bash / nohup / pkill / pidfile

## Global Constraints

- 停 Docker 中间件:**不做**(--stop 只停应用层;需停时 `cd infra && docker compose down`)
- worker 停止沿用带 `WORKER_TAG` 的 pkill,只命中本 checkout,不误杀 `/home/wjyy2`
- 不改模型/后端/worker 启动逻辑本身;只改入口编排(参数、trap、wait)
- `--stop` 判断必须放在 `cleanup()` 函数定义**之后**(bash 函数需先定义后调用)
- 静态检查:`bash -n start.sh`、`bash -n start_front.sh` 退出 0
- 运行测试:无 pytest 覆盖(纯 shell);以静态检查 + grep 验证 + 手工冒烟为准
- **本实现不自动 commit(用户自行 commit)**

---

### Task 1: `start.sh` 一键部署改造

**Files:**
- Modify: `start.sh`(参数解析段、cleanup 后加 --stop 分支、删 trap、删末尾 wait、更新文件头用法注释)

**Interfaces:**
- Consumes: 现有 `cleanup()`(58-90 行,已含 WORKER_TAG 加固)
- Produces: `bash start.sh` = 启动后返回;`bash start.sh --stop` = 停应用层全部服务并退出

- [ ] **Step 1: 更新文件头用法注释**

把第 9-14 行:

```bash
# 用法：
#   bash start.sh              # 启动全部
#   bash start.sh --no-models  # 跳过模型服务（仅中间件+后端）
#   bash start.sh --no-ocr     # 跳过 PaddleOCR
#   bash start.sh --no-medgo   # 跳过 MedGo
```

改为:

```bash
# 用法：
#   bash start.sh              # 启动全部后返回 shell(服务后台常驻)
#   bash start.sh --stop       # 停止应用层全部服务(Docker 中间件保持运行)
#   bash start.sh --no-models  # 跳过模型服务（仅中间件+后端）
#   bash start.sh --no-ocr     # 跳过 PaddleOCR
#   bash start.sh --no-medgo   # 跳过 MedGo
```

- [ ] **Step 2: 参数解析段加 --stop 标记**

把第 45-54 行:

```bash
# 解析参数
SKIP_MODELS=0; SKIP_OCR=0; SKIP_MEDGO=0; SKIP_EMBED=0; SKIP_RERANKER=0
for arg in "$@"; do
  case "$arg" in
    --no-models)   SKIP_MODELS=1; SKIP_OCR=1; SKIP_MEDGO=1; SKIP_EMBED=1; SKIP_RERANKER=1 ;;
    --no-ocr)      SKIP_OCR=1 ;;
    --no-medgo)    SKIP_MEDGO=1 ;;
    --no-embed)    SKIP_EMBED=1 ;;
    --no-reranker) SKIP_RERANKER=1 ;;
  esac
done
```

改为:

```bash
# 解析参数
SKIP_MODELS=0; SKIP_OCR=0; SKIP_MEDGO=0; SKIP_EMBED=0; SKIP_RERANKER=0
STOP=0
for arg in "$@"; do
  case "$arg" in
    --stop)        STOP=1 ;;
    --no-models)   SKIP_MODELS=1; SKIP_OCR=1; SKIP_MEDGO=1; SKIP_EMBED=1; SKIP_RERANKER=1 ;;
    --no-ocr)      SKIP_OCR=1 ;;
    --no-medgo)    SKIP_MEDGO=1 ;;
    --no-embed)    SKIP_EMBED=1 ;;
    --no-reranker) SKIP_RERANKER=1 ;;
  esac
done
```

- [ ] **Step 3: cleanup 定义后加 --stop 分支,并删 trap**

把第 88-91 行:

```bash
  log "Done. Docker 中间件保持运行（如需停止：cd $INFRA_DIR && docker compose down）"
  exit 0
}
trap cleanup SIGINT SIGTERM
```

改为:

```bash
  log "Done. Docker 中间件保持运行（如需停止：cd $INFRA_DIR && docker compose down）"
  exit 0
}

# --stop:显式停服(cleanup 内部含 exit 0;Docker 中间件保持运行)
if [[ "$STOP" == "1" ]]; then
  cleanup
fi
```

> 删掉了 `trap cleanup SIGINT SIGTERM`,启动路径不再被外部 SIGTERM 触发全杀。`--stop` 需在 cleanup 定义后判断,故此处放置。

- [ ] **Step 4: 删除末尾 wait**

删除第 370-371 行的空行与 `wait`,使脚本以 §9 横幅后的 `echo ""` 结尾(保留末尾空行即可)。

- [ ] **Step 5: 静态检查**

Run: `bash -n start.sh`
Expected: 退出 0,无输出
Run: `grep -n "trap cleanup\|^wait\|wait$\|STOP" start.sh`
Expected: 仅见 `STOP`(3 处:初始化、case、if 判断);**无** `trap cleanup`、**无** 末尾 `wait`
Run: `tail -5 start.sh`
Expected: 以 §9 汇总横幅结尾,无 `wait`

Commit: 跳过(用户自行 commit)

---

### Task 2: `start_front.sh` 一键部署改造

**Files:**
- Modify: `start_front.sh`(参数解析段、cleanup 后加 --stop 分支、删 trap、删末尾 wait、更新文件头用法注释与横幅 Stop 行)

**Interfaces:**
- Consumes: 现有 `cleanup()`(48-56 行,处理 `/tmp/start-front-*.pid`)
- Produces: `bash start_front.sh` = 启动后返回;`bash start_front.sh --stop` = 停前端三门户

- [ ] **Step 1: 更新文件头用法注释**

把第 7-12 行:

```bash
# 用法：
#   bash start_front.sh              # 启动全部三个
#   bash start_front.sh --user       # 仅用户端
#   bash start_front.sh --doctor     # 仅医生端
#   bash start_front.sh --admin      # 仅管理后台
```

改为:

```bash
# 用法：
#   bash start_front.sh              # 启动全部三个后返回 shell
#   bash start_front.sh --stop       # 停止前端三门户
#   bash start_front.sh --user       # 仅用户端
#   bash start_front.sh --doctor     # 仅医生端
#   bash start_front.sh --admin      # 仅管理后台
```

- [ ] **Step 2: 参数解析段加 --stop 标记**

把第 24-30 行:

```bash
for arg in "$@"; do
  case "$arg" in
    --user)   HAS_FILTER=1; START_USER=1;   START_DOCTOR=0; START_ADMIN=0 ;;
    --doctor) HAS_FILTER=1; START_USER=0;   START_DOCTOR=1; START_ADMIN=0 ;;
    --admin)  HAS_FILTER=1; START_USER=0;   START_DOCTOR=0; START_ADMIN=1 ;;
  esac
done
```

改为:

```bash
STOP=0
for arg in "$@"; do
  case "$arg" in
    --stop)   STOP=1 ;;
    --user)   HAS_FILTER=1; START_USER=1;   START_DOCTOR=0; START_ADMIN=0 ;;
    --doctor) HAS_FILTER=1; START_USER=0;   START_DOCTOR=1; START_ADMIN=0 ;;
    --admin)  HAS_FILTER=1; START_USER=0;   START_DOCTOR=0; START_ADMIN=1 ;;
  esac
done
```

- [ ] **Step 3: cleanup 定义后加 --stop 分支,并删 trap**

把第 54-57 行:

```bash
  log "Done."
  exit 0
}
trap cleanup SIGINT SIGTERM
```

改为:

```bash
  log "Done."
  exit 0
}

# --stop:显式停服(cleanup 内部含 exit 0)
if [[ "$STOP" == "1" ]]; then
  cleanup
fi
```

> 注意:此 if 须在 cleanup 定义后,故放在此处(cleanup 定义于 48 行)。原 trap 删除。

- [ ] **Step 4: 删末尾 wait,并改横幅 Stop 行**

把第 114 行 `echo "  Stop: Ctrl+C"` 改为 `echo "  Stop: bash start_front.sh --stop"`,并删除第 118 行 `wait`。

- [ ] **Step 5: 静态检查**

Run: `bash -n start_front.sh`
Expected: 退出 0,无输出
Run: `grep -n "trap cleanup\|^wait\|wait$\|STOP\|--stop" start_front.sh`
Expected: `STOP` 3 处 + 横幅 `--stop` 引用;无 `trap cleanup`、无末尾 `wait`

Commit: 跳过(用户自行 commit)

---

### Task 3: AGENTS.md 用法说明 + 冒烟验证

**Files:**
- Modify: `AGENTS.md`(「批量并行 worker」小节后补一小节)

- [ ] **Step 1: AGENTS.md 补启动用法**

在「批量并行 worker(2026-09-03 起)」小节之后、「RabbitMQ vhost」小节之前插入:

```markdown
## start.sh 启动模式(2026-09-03 起)

**事实**: `start.sh` 已从「前台阻塞 + Ctrl+C 全杀」改为「**启动即返回 shell + 独立 `--stop`**」。

- `bash start.sh` = 启动 Docker 中间件 + 模型 + 后端 + workers,打印完成横幅后**返回 shell**(服务 nohup 后台常驻)。不再有末尾 `wait`,不再前台阻塞。
- `bash start.sh --stop` = 停应用层全部服务(模型/后端/workers,worker 按 `WORKER_TAG` 只停本 checkout);**Docker 中间件保持运行**(需停: `cd infra && docker compose down`)。
- 不再 `trap cleanup SIGINT SIGTERM`:启动途中 Ctrl+C 只中断脚本本身,nohup 子进程不受影响;停服一律用 `--stop`。
- `start_front.sh` 同构:`bash start_front.sh` 启动返回、`bash start_front.sh --stop` 停前端三门户。
- 模型/后端/worker 的启动逻辑与 GPU 分配未变,见上文各节。
```

- [ ] **Step 2: 冒烟验证(服务当前全 UP 环境)**

Run:
```bash
cd /data/project/hospitalKnowledgeBase
timeout 120 bash start.sh | tail -20   # 各段守卫跳过,应几秒内打印横幅并返回(此前卡 wait)
```
Expected: 打印完整横幅、命令在几十秒内返回(模型已 UP,curl 守卫秒过;若未传 --stop 不阻塞)
Run:
```bash
bash start.sh --stop
for p in 8000 8001 8002 8003 8004; do printf ":%s " "$p"; curl -s -m2 http://localhost:$p/health >/dev/null 2>&1 && echo UP || echo DOWN; done
```
Expected: 全部 DOWN;无 start.sh 残留进程(`ps -eo pid,args | grep start.sh | grep -v grep`)
Run:
```bash
bash start.sh   # 一键重启
for p in 8000 8001 8002 8003 8004; do printf ":%s " "$p"; curl -s -m2 http://localhost:$p/health >/dev/null 2>&1 && echo UP || echo DOWN; done
ps -eo pid,args | grep 'start_worker() # /data/project' | grep -v grep | wc -l   # 期望 6
```
Expected: 全部 UP;worker=6;`/home/wjyy2` 不受影响(其 worker 无 WORKER_TAG 标记,不被本次 --stop 命中)
> 冒烟会实际停/起服务;若不想动当前运行态,可跳过 Step 2 只做 Step 1 + 静态检查,由用户自行验证。

Commit: 跳过(用户自行 commit)

---

## Self-Review

**Spec coverage:**
- spec §1 启动/停止/trap 三行为 → Task 1(start.sh)+ Task 2(front)
- spec §2 代码改动 2.1/2.2/2.3 → Task 1/2/3 Step 1
- spec §3 静态 + 冒烟 → Task 1 Step 5、Task 2 Step 5、Task 3 Step 2
- spec §4/§5 回滚与 commit → Global Constraints 末行

**Placeholder scan:** 无 TBD/TODO;每个改动点给出前/后完整文本与验证命令。

**Type consistency:** `--stop` 在 start.sh 与 start_front.sh 语义一致(均调用各自 cleanup 内含 exit 0);Task 1/2 中 STOP 标记的三处(初始化/case/if)与 Task 3 文档描述一致;删除对象(文件名、行号、trap 串、wait)均与当前文件实际内容核对过。
