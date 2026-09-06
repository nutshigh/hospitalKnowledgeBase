# start.sh 一键部署改造(启动即返回 + 独立 --stop) 设计

**日期**:2026-09-03
**状态**:Draft(用户已对齐三处决策:入口=start.sh --stop、Docker 不动、前台=启动即返回)
**前置**:
- 并行 worker 改造(已在同分支,start.sh 已带 WORKER_* / WORKER_TAG / ensure_workers)
- 工程约束:`AGENTS.md`

---

## 0. 目标与边界

### 目标
把 `start.sh` 从「前台阻塞式(末尾 `wait` 卡住 → 靠 Ctrl+C 触发 `cleanup()` 全杀)」改成「**启动即返回 shell + 独立 `--stop` 停服**」的标准一键部署形态。避免两个真实事故:
1. 前台跑完以为结束,按 Ctrl+C 触发 `cleanup()` 把**全部模型/后端 worker 杀掉**;
2. 后台跑时父进程被 SIGTERM(工具超时/外部 kill)同样触发 cleanup 全杀——`wait` 期间的任何 SIGTERM 都是"核弹开关"。

### 根因(现状)
- `start.sh` 末尾 `wait`(start.sh:371)等所有 nohup 后台子进程(模型服务永不退出)→ 脚本永不返回。
- `trap cleanup SIGINT SIGTERM`(start.sh:91):前台 Ctrl+C 或外部 SIGTERM → `cleanup()` 顺序 kill pidfile 服务 + pkill 模型/后端/worker。设计上"Ctrl+C 停全部",但**任何拿到该进程的 SIGTERM 都等效核弹**,且与"部署完想留在后台"的诉求冲突。
- `start_front.sh` 同构(末尾 wait + trap cleanup)。

### 范围内
- `start.sh`:`--stop` 参数触发"停应用层服务"(复用现 cleanup 主体,去掉 exit 语义调整);默认启动路径去掉末尾 `wait`,跑完打印横幅即退出;`trap` 语义改为安全(见 §1)。
- `start_front.sh`:`--stop` + 去掉 wait(与 start.sh 对齐,避免同样坑)。
- AGENTS.md 补充一行说明新用法。
- 冒烟验证(不 commit)。

### 范围外(YAGNI)
- 停 Docker 中间件(MySQL/RabbitMQ/Milvus/Neo4j):--stop 明确**不动** Docker(数据不丢、重启快;需停时 `cd infra && docker compose down`)。
- 进程守护/自拉起(worker watchdog 之类)。
- 修改模型/后端/worker 启动本身(只改入口编排)。

---

## 1. 行为设计

### 启动(默认)
`bash start.sh [--no-models|--no-ocr|--no-medgo|--no-embed|--no-reranker]`
- 行为与现在启动路径**完全一致**(docker → 模型 → 后端 → worker),只是**不再末尾 `wait`**,打印完 §9 横幅即退出,返回 shell。
- 服务均为 `nohup ... &` 启动,已脱离父 shell,脚本退出不影响它们。
- 模型就绪仍由启动路径内各段 curl 轮询保障(§5),与现在相同。

### 停止(新)
`bash start.sh --stop`
- 复用现有 `cleanup()` 主体(pidfile kill + 带 WORKER_TAG 的 pkill worker + 模型/后端 pkill),停掉**应用层全部服务**,打印完成信息,退出。
- 明确提示 Docker 中间件保持运行(`cd infra && docker compose down` 可停)。
- 与当前 Ctrl+C 触发的是**同一套清理逻辑**,故停止能力与现状等价,只是改由显式参数触发。

### trap 安全
- 启动路径:去掉 `trap cleanup SIGINT SIGTERM`。理由:脚本不再阻塞在 wait,正常几秒内跑完退出;若用户在启动过程中 Ctrl+C,只中断脚本本身,nohup 子进程已脱离不受影响(避免"启动一半 Ctrl+C 把已起服务全杀")。彻底移除"任何 SIGTERM = 核弹"的隐患。
- `--stop` 路径:解析参数后**显式调用** `cleanup()`(内部已含 `exit 0`)。

### 幂等
- 启动幂等:各段现有守卫不变(docker compose ps / curl health / ensure_workers pgrep 计数补差)。
- 停止幂等:`pkill ... || true`、kill 已死 pid 忽略、rm -f 不存在 pidfile 无副作用;重复 --stop 安全。

---

## 2. 代码改动

### 2.1 `start.sh`

**(a) 参数解析段(第 45-54 行)** — 增加:
```bash
STOP=0
for arg in "$@"; do
  case "$arg" in
    --stop) STOP=1 ;;
    --no-models) ...
  esac
done
if [[ "$STOP" == "1" ]]; then
  cleanup
fi
```
> 注意:cleanup 定义在 58-90 行、trap 在 91 行。`--stop` 分支须在 cleanup 定义**之后**解析,或在 cleanup 定义后放置。参数解析目前在第 44-54 行(早于 cleanup),故需把 stop 判断挪到 cleanup 定义后,或在解析时只设 `STOP` 标记、定义后再执行。实现时保证顺序正确。

**(b) `trap cleanup SIGINT SIGTERM`(第 91 行)** — 删除(启动不再被外部 SIGTERM 全杀)。

**(c) 末尾 `wait`(第 371 行)** — 删除(启动即返回)。

### 2.2 `start_front.sh`
- 同构改造:参数解析加 `--stop` → 调其 `cleanup()`;删除 `trap cleanup SIGINT SIGTERM`(第 57 行附近);删除末尾 `wait`(第 118 行)。
- front 的 cleanup 只处理 `/tmp/start-front-*.pid`(npm run dev 进程),不含 Docker。

### 2.3 AGENTS.md
- 在「批量并行 worker」或新增一小节记录:`bash start.sh` = 启动后返回 shell(不再前台阻塞);`bash start.sh --stop` = 停应用层服务(Docker 保持);前台 Ctrl+C 只中断脚本,不再全杀服务。

---

## 3. 验证

### 静态
- `bash -n start.sh` / `bash -n start_front.sh` 退出 0。
- grep:start.sh 无 `^wait` 结尾、无 `trap cleanup`;start_front.sh 同样。

### 冒烟(在现有已启动环境执行,不 commit)
1. `bash start.sh`(服务已 UP,各段守卫快速跳过)→ 应**几秒内打印横幅并返回 shell**(此前会卡 wait)。
2. `bash start.sh --stop` → 停掉后端/模型/worker;`for p in 8000 8001 8002 8003 8004; do curl ...` 应 DOWN。
3. 再次 `bash start.sh` 一键拉起 → 全部 UP、worker=2/3/1。
4. `ps -eo pid,args | grep start.sh` 无残留;`/home/wjyy2` worker 不受影响(带 tag 匹配)。

---

## 4. 回滚
- `git checkout -- start.sh start_front.sh` 即恢复前台阻塞模式;无 DB 迁移。

## 5. commit
- **由用户执行**(manual-commit mode,本实现不 commit)。
