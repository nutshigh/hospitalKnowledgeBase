# 批量报告处理水平扩进程(并行 worker)设计

**日期**:2026-09-03
**状态**:Draft(用户已批准整体方案;修订:①计数原子化由实现者自评避免死锁 ②默认值直接改,不以环境变量为默认 1)
**前置**:
- 批量上传 + RabbitMQ worker 管线:`docs/superpowers/specs/2026-07-15-batch-report-upload-design.md`
- 跨院分发批次进度:`AGENTS.md`「批量上传跨院分发」小节
- 工程约束:`AGENTS.md`

---

## 0. 目标与边界

### 目标
缩短单批(几百份报告)批量上传的墙钟耗时。当前每类 worker 在 `start.sh` 只起 **1 个进程**,进程内单 channel、prefetch=1,**一次只处理一条消息** → parsing/interpretation 全批串行。RabbitMQ 对同一 queue 的多个 consumer 天然 round-robin,水平扩进程即可并行,且无需改动消息格式/业务表。

### 根因(现状)
- `report/worker.py::start_worker` 与 `interpretation/worker.py::start_worker`:单进程 `rabbitmq.consume(...)` + `start_consuming()`,prefetch=1(rabbitmq.py:139),串行一条条处理。
- `start.sh` §7:每类 worker `pgrep` 命中即跳过,只保证"起 1 个"(start.sh:291-322)。
- 真实瓶颈:MedGo vLLM 单实例(TP=4, 4×L20)。多 worker 并发时 vLLM continuous batching,利用率上升 → 墙钟下降。

### 并发正确性隐患(必须修)
`BatchService.increment_progress` 对批次计数做 **read-modify-write**(batch_service.py:196-198):
```python
before = getattr(b, field) or 0
setattr(b, field, before + 1)
db.commit()
```
file 行的状态推进走条件 UPDATE(幂等门,结果 0 行即跳过),但**批次计数器**是"SELECT 旧值 → 应用内 +1 → UPDATE 写回"。两个 worker 并发收尾同批次**不同文件**时,可能同时读到同一旧值各自 +1 → **丢一次计数**;`interp_ok + failed < total` 永真,批次卡死在 `interpreting`。sweeper 也只重跑 `_maybe_advance_status`,基于同一份错误计数 → **无法自愈**。

### 范围内
- `BatchService.increment_progress` 计数改为**原子 SQL 自增**,避免丢计数与死锁(见 §1 锁序分析)
- `start.sh` §7:每类 worker 按 `WORKER_PARSE / WORKER_INTERP / WORKER_EXTRACT` **计数补差**,默认值直接改(parse=2、interp=3、extract=1)
- worker 命令带当前 checkout 唯一标记,`pgrep/pkill` 只命中 `/data/project` 自己起的进程,**不误杀旧 checkout `/home/wjyy2` 下隔离运行的那套**
- 对应 pytest 单测(幂等/并发/状态机回归)

### 范围外(YAGNI)
- 改 worker 消费代码 / 消息格式 / DB 表结构 —— 纯水平扩 + 计数原子化
- extract worker 内部"单压缩包多文件"的线程级并行(瓶颈在 interpretation)
- 放开 bulk 时段窗口(`BULK_WINDOW_START/END`)
- 调整 `MEDGO_MAX_CONCURRENCY`(每进程 asyncio sem=2):跨进程并发已够;将来需要再把该值作为单进程内上限单独调
- `report/worker.py` 内同报告并发去重:单份报告只发一条消息,同一 queue 消息只会被一个 consumer 取走,无需额外协调;interpretation 已有 F15 running-skip(interpretation/worker.py:36)防重复跑

---

## 1. 计数原子化:`increment_progress`

### 现状(伪码)
```python
# 1) file 行条件 UPDATE(已原子)
result = UPDATE batch_import_file SET status=:new WHERE id=:fid AND status NOT IN (:prior_ok)
# 2) 批次计数 read-modify-write(竞态点)
b = SELECT * FROM batch_import WHERE id=:batch_id
setattr(b, field, (b.field or 0) + 1)
b.updated_at = now
COMMIT
```

### 目标(伪码)
```python
# 1) 不变(幂等门,0 行→return)
result = UPDATE batch_import_file SET status=:new WHERE id=:fid AND status NOT IN (:prior_ok)
if result == 0: return
# 2) 原子自增,由 DB 行锁保证不丢
UPDATE batch_import SET <field> = <field> + 1, updated_at = now WHERE id=:batch_id
# 3) 刷新本地 batch 对象,再跑 _maybe_advance_status(读最新计数决策)
b = SELECT * FROM batch_import WHERE id=:batch_id
_maybe_advance_status(db, b)
COMMIT
```

### 锁序与死锁分析(自评结论)
两条语句的加锁顺序**固定且一致**:
1. 先锁 `batch_import_file` 行(条件 UPDATE,只锁命中行)
2. 再锁 `batch_import` 的**同一行**(按 batch_id 的原子 UPDATE)

多进程并发收尾同一批次不同文件时:
- 每事务持锁集合都是 `{自己的 file 行} ∪ {batch 行}`;
- 竞争只在 batch 行:file 行互不重叠,不会出现"进程 A 持 batch 锁等 file 锁、进程 B 持 file 锁等 batch 锁"的环 —— **无死锁**,只会让后来者等行锁,等待极短(一条 UPDATE)。
- 原子 `SET field = field + 1` 在 MySQL InnoDB 是行锁下的读改写,不会丢计数。
- `_maybe_advance_status` 在自增提交前后都会执行;同一事务内 refresh 后读到的是最新计数,终态判定 `interp_ok + failed == total` 可正确触发。状态推进本身幂等(`old in terminal → return`)。

> 用 SQLAlchemy Core `update().values({col: col + 1})`(列表达式自增),而不是 ORM 属性读改写。测试环境(sqlite :memory:)同样支持列自增表达式。

---

## 2. `start.sh` worker 计数补差

### 现状
每类 worker 一段独立 `if pgrep ...; then skip; else nohup 起 1 个`。

### 目标
在 §7 引入按类型计数补差循环,并让 `cleanup()`/重复启动判断**只命中本 checkout**:

- 顶部读环境变量(默认直接改):
  ```bash
  export WORKER_PARSE=${WORKER_PARSE:-2}
  export WORKER_INTERP=${WORKER_INTERP:-3}
  export WORKER_EXTRACT=${WORKER_EXTRACT:-1}
  ```
- worker 启动命令的 `-c` 串内拼入 checkout 唯一标记(如 `# <BACKEND_DIR>` 注释),使进程 cmdline 可被 `pgrep -f "<module> # <BACKEND_DIR>"` 精确匹配。
- 每类 worker:
  ```
  running = pgrep -f "app.modules.<X>.worker # $BACKEND_DIR" | wc -l
  need = WORKER_<X>
  while running < need: 起 1 个,log = /data/logs/worker-<name>.<idx>.stdout.log
  # running > need:不主动杀(缩容由 cleanup/手动处理),只 log 提示
  ```
- `cleanup()` 与 worker 启动判断全部改用带 `$BACKEND_DIR` 标记的 `pkill -f`,不碰 `/home/wjyy2/hospitalKnowledgeBase/backend` 下隔离运行的旧 worker。

### 交互验证
- 起后 `ps -eo pid,args | grep <marker>` 看到 parse×2、interp×3、extract×1。
- RabbitMQ 队列 consumer 数(`GET /api/v1/dispatch/queues` 或 `rabbitmqctl list_consumers`)与 worker 数一致。

---

## 3. 测试

### 单测(pytest,backend/tests)
- `test_batch_service.py`:
  - 保留 `test_increment_progress_idempotent`(幂等门仍生效:同 file 重复 parsed_ok 只计 1 次)
  - 保留 `test_parsed_to_interp_ok_progression`(状态机 parsing→interpreting→completed 回归)
  - 新增 **并发不丢计数**:模拟两文件在不同事务中先后调用原子自增(串行复现已能覆盖语义;若 sqlite 内存库可开两连接再断言 `interp_ok==2`)
- `test_batch_cross_hospital.py`:`update_batch_progress` 同/跨库路由断言保持通过(mock 层不受原子化影响)
- `test_report_worker_bulk.py` / `test_interp_worker_bulk.py`:worker 成功路径 `parsed_ok`/`interp_ok` 计数断言保持通过
- `test_batch_sweeper.py`:状态推进回归不受影响

### 手工冒烟(本次不做自动化)
- `WORKER_PARSE=2 WORKER_INTERP=3`(默认值已是)后 `bash start.sh`,核对 worker 进程数 / queue consumer 数 / 单批墙钟对比。

---

## 4. 明确不做与回滚
- 本设计不改 worker 消费逻辑、不改表结构、不动默认 bulk 窗口。
- 回滚:改回 `start.sh` worker 计数为"起 1 个" + 还原原子化即可,无需 DB 迁移。
- **commit 由用户执行**(本次改动较大,交付后由用户自行 review + commit)。
