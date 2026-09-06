# 批量报告处理水平扩进程(并行 worker) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让批量上传 parsing/interpretation 由多进程并行消费 RabbitMQ 队列,缩短单批墙钟;并修复多进程并发收尾同一批次时的计数丢失隐患。

**Architecture:** 纯水平扩——每类 worker 进程内仍是单消费者 prefetch=1,RabbitMQ 对同一 queue 的多个 consumer 自动 round-robin,业务逻辑零改动。唯一必须的配套改动是 `BatchService.increment_progress` 的批次计数从「ORM 读-改-写」改成 SQL 层自增(MySQL 行锁串行化,不丢计数、无死锁)。`start.sh` 读 `WORKER_PARSE/WORKER_INTERP/WORKER_EXTRACT`(默认 2/3/1)按需补足 worker 进程,并在 worker cmdline 尾部注入 `# <BACKEND_DIR>` 唯一标记,使 `pgrep/pkill` 只命中本 checkout,不误杀其它 checkout(如 `/home/wjyy2/hospitalKnowledgeBase`)隔离运行的 worker。

**Tech Stack:** Python 3.10 / SQLAlchemy (ORM + Core update) / MySQL(InnoDB 行锁) / Bash / RabbitMQ / pytest(sqlite)

## Global Constraints

- 默认并发:`WORKER_PARSE=2`、`WORKER_INTERP=3`、`WORKER_EXTRACT=1`(直接写默认值,可环境变量覆盖)
- worker cmdline 一律带唯一标记 `# $WORKER_TAG`(`WORKER_TAG=$BACKEND_DIR`),所有 `pgrep/pkill` 判定必须带该标记,**禁止**再用裸 `pkill -f "app.modules.report.worker"` 之类跨 checkout 匹配
- 计数自增必须是 SQL 层表达式 `SET <col> = <col> + 1`(引用列自身),禁止 ORM 属性读改写
- 加锁顺序固定:先 `batch_import_file` 行 → 再 `batch_import` 行,不允许反转(避免死锁环)
- 不改 worker 消费代码、不改消息格式、不改 DB 表结构、不动 bulk 窗口(`BULK_WINDOW_START/END`)、不动 `MEDGO_MAX_CONCURRENCY`
- 运行测试:`cd backend && .venv/bin/pytest tests/test_batch_service.py tests/test_batch_cross_hospital.py tests/test_report_worker_bulk.py tests/test_interp_worker_bulk.py tests/test_batch_sweeper.py -q`
- **本实现不自动 commit(用户自行 commit)**

---

### Task 1: `increment_progress` 批次计数原子化

**Files:**
- Modify: `backend/app/modules/report/batch_service.py:165-202`(`increment_progress`)
- Test: `backend/tests/test_batch_service.py`(新增两个测试;现有用例全部保持通过)

**Interfaces:**
- Consumes: 无(纯内部实现)
- Produces: `BatchService.increment_progress(db, batch_id, file_id, field, stage=None)` 签名与返回不变(无返回值);行为不变,仅计数写回改为 SQL 自增

- [ ] **Step 1: 先加失败用例(捕获 SQL 形态,防回退为读-改-写)**

在 `backend/tests/test_batch_service.py` 末尾追加:

```python
def test_increment_counter_uses_db_side_self_increment(db):
    """批次计数必须是 SQL 层自增(SET col = col + 1),而不是 ORM 先读后写。

    多 worker 并发收尾同批次不同文件时,读-改-写会丢计数使批次卡 interpreting;
    此断言防止未来回退成 ORM setattr 写法。
    """
    import re
    from sqlalchemy import event

    db.add(BatchImport(id="b1", hospital_id="H", user_id="u",
                       filename="x", archive_path="/x"))
    db.add(BatchImportFile(id="f1", batch_id="b1", file_path="a",
                           file_size=1, crc32="abc12345"))
    db.commit()

    stmts = []
    engine = db.get_bind()

    def _capture(conn, cursor, statement, parameters, context, executemany):
        stmts.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        BatchService.increment_progress(db, "b1", "f1", "parsed_ok")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    batch_updates = [s for s in stmts
                     if "batch_import" in s.lower() and "set" in s.lower()]
    assert batch_updates, "expected at least one UPDATE on batch_import"
    assert re.search(r"parsed_ok\s*=\s*(?:batch_import\.)?parsed_ok\s*\+\s*\?",
                     batch_updates[-1]), f"expected self-increment, got: {batch_updates[-1]}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_batch_service.py::test_increment_counter_uses_db_side_self_increment -q`
Expected: FAIL,`assert batch_updates` 或正则不匹配(现状 `batch_import.parsed_ok = ?` 带一个参数,非自增)

- [ ] **Step 3: 实现原子化**

把 `backend/app/modules/report/batch_service.py:196-199` 这一段:

```python
        before = getattr(b, field) or 0
        setattr(b, field, before + 1)
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        _log.info("increment_progress batch=%s fid=%s field=%s stage=%s count=%d->%d",
                   batch_id, file_id, field, stage or "-", before, before + 1)
        BatchService._maybe_advance_status(db, b)
```

替换为:

```python
        # 批次计数走 SQL 层自增(同一事务内,先锁 file 行、再锁 batch 行,顺序固定无死锁;
        # 并发 worker 收尾同批次不同文件时由 DB 行锁串行化,不丢计数)。
        col = BatchImport.__table__.c[field]
        db.execute(
            BatchImport.__table__.update()
            .where(BatchImport.__table__.c.id == batch_id)
            .values({col: col + 1,
                     BatchImport.__table__.c.updated_at: datetime.now(timezone.utc)})
        )
        b = db.query(BatchImport).get(batch_id)
        db.commit()
        if b is None:
            _log.warning("increment_progress batch_not_found batch=%s fid=%s field=%s",
                          batch_id, file_id, field)
            return
        db.refresh(b)
        _log.info("increment_progress batch=%s fid=%s field=%s stage=%s count=%s",
                   batch_id, file_id, field, stage or "-", getattr(b, field) or 0)
        BatchService._maybe_advance_status(db, b)
```

> 说明:上面的 `col = BatchImport.__table__.c[field]` 中 `field ∈ {"parsed_ok","interp_ok","failed"}`,列均在表内;`col + 1` 编译为 `SET parsed_ok = parsed_ok + ?`。file 行条件 UPDATE(行 181-184)保持不变,仍是幂等门。`db.refresh(b)` 让随后 `_maybe_advance_status` 读到事务内最新计数。变量 `b` 在替换前已于上文第 190 行定义,替换段内重新 `get` 赋值覆盖原对象。

- [ ] **Step 4: 跑新增 + 回归测试**

Run: `cd backend && .venv/bin/pytest tests/test_batch_service.py tests/test_batch_cross_hospital.py tests/test_report_worker_bulk.py tests/test_interp_worker_bulk.py tests/test_batch_sweeper.py -q`
Expected: 全 PASS(含既有 `test_increment_progress_idempotent`、`test_parsed_to_interp_ok_progression`、跨院路由、worker bulk 计数断言)

- [ ] **Step 5: 追加一个「不同事务不互相覆盖」行为用例(选做,防御性)**

在 `test_batch_service.py` 追加:

```python
def test_increment_two_files_in_separate_sessions_no_lost_update(db):
    """两个文件分属不同会话各 increment 一次,计数应为 2(不被后者覆盖)。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = db.get_bind()
    Session = sessionmaker(bind=engine)

    db.add(BatchImport(id="b1", hospital_id="H", user_id="u",
                       filename="x", archive_path="/x"))
    db.add(BatchImportFile(id="f1", batch_id="b1", file_path="a",
                           file_size=1, crc32="abc12345"))
    db.add(BatchImportFile(id="f2", batch_id="b1", file_path="b",
                           file_size=1, crc32="def45678"))
    db.commit()

    s1 = Session(); s2 = Session()
    try:
        BatchService.increment_progress(s1, "b1", "f1", "interp_ok")
        BatchService.increment_progress(s2, "b1", "f2", "interp_ok")
    finally:
        s1.close(); s2.close()

    db.expire_all()
    assert db.query(BatchImport).get("b1").interp_ok == 2
```

Run: `cd backend && .venv/bin/pytest tests/test_batch_service.py -q`
Expected: PASS。若 Step 4 已跑过,此步只需再跑 `test_batch_service.py`。

- [ ] **Step 6: 全量 batch 相关测试(最终确认)**

Run: `cd backend && .venv/bin/pytest tests/test_batch_service.py tests/test_batch_cross_hospital.py tests/test_report_worker_bulk.py tests/test_interp_worker_bulk.py tests/test_batch_sweeper.py tests/test_batch_router.py tests/test_batch_models.py -q`
Expected: 全 PASS
Commit: 跳过(用户自行 commit)

---

### Task 2: `start.sh` worker 计数补差 + checkout 唯一标记

**Files:**
- Modify: `start.sh`
  - 顶部(第 27 行 `export LOG_LEVEL` 后)插入并发默认值与 `WORKER_TAG`
  - `cleanup()`(第 60-62 行)三行裸 `pkill` 改为带标记匹配
  - §7 workers(第 290-322 行)整块改为按计数补足

**Interfaces:**
- Consumes: `BACKEND_DIR`(第 18 行已定义)
- Produces: 环境变量 `WORKER_PARSE`(默认2)/`WORKER_INTERP`(默认3)/`WORKER_EXTRACT`(默认1)/`WORKER_TAG`;shell 函数 `ensure_workers <模块段> <显示名> <目标数> <日志名前缀>`
- 启动后进程数:`app.modules.report.worker`×2、`app.modules.interpretation.worker`×3、`app.modules.report.extract_worker`×1;每进程 cmdline 尾部含 ` # $WORKER_TAG`

- [ ] **Step 1: 顶部插入默认值与标记**

在第 27 行 `export LOG_LEVEL=${LOG_LEVEL:-INFO}` 之后、`mkdir -p /data/logs` 之前插入:

```bash
# 批量处理 worker 并发(每类进程数;默认 parse=2 / interp=3 / extract=1,可环境变量覆盖)
export WORKER_PARSE="${WORKER_PARSE:-2}"
export WORKER_INTERP="${WORKER_INTERP:-3}"
export WORKER_EXTRACT="${WORKER_EXTRACT:-1}"
# 本 checkout 唯一标记:拼进 worker cmdline,保证 pgrep/pkill 只命中本目录起的 worker,
# 不误杀其它 checkout(如 /home/wjyy2/hospitalKnowledgeBase)隔离运行的旧 worker。
export WORKER_TAG="$BACKEND_DIR"
```

- [ ] **Step 2: 改 `cleanup()` 的 worker pkill 为带标记**

把第 60-62 行:

```bash
  pkill -f "app.modules.report.worker" 2>/dev/null || true
  pkill -f "app.modules.interpretation.worker" 2>/dev/null || true
  pkill -f "app.modules.report.extract_worker" 2>/dev/null || true
```

替换为:

```bash
  pkill -f "from app.modules.report.worker import start_worker; start_worker\\(\\) # $WORKER_TAG" 2>/dev/null || true
  pkill -f "from app.modules.interpretation.worker import start_worker; start_worker\\(\\) # $WORKER_TAG" 2>/dev/null || true
  pkill -f "from app.modules.report.extract_worker import start_worker; start_worker\\(\\) # $WORKER_TAG" 2>/dev/null || true
```

> `start_worker\\(\\)` 是 ERE 转义,匹配字面 `start_worker()`;`# $WORKER_TAG` 保证只命中带本 checkout 标记的 worker。旧 checkout(/home/wjyy2)起的 worker 无此标记 → 不被误杀。第一段 pidfile 清理循环(51-54 行)不变,照旧 kill `/tmp/start-sh-*.pid`。

- [ ] **Step 3: 新增 `ensure_workers` 辅助函数**

在 §7 块之前(即 `# ── 7. RabbitMQ Workers ─...` 注释与第 291 行之间)插入:

```bash
# 确保某类 worker 起够 N 个(不足则补足;带 WORKER_TAG 标记精确匹配本 checkout)
ensure_workers() {
  local module="$1" label="$2" want="$3" lname="$4" have i
  have=$(pgrep -f "from app.modules.$module import start_worker; start_worker\\(\\) # $WORKER_TAG" 2>/dev/null | wc -l)
  have=${have:-0}
  log "  $label:目标 $want,当前 $have"
  i=$((have + 1))
  while [ "$i" -le "$want" ]; do
    cd "$BACKEND_DIR"
    nohup $VENV/python -u -c "from app.modules.$module import start_worker; start_worker() # $WORKER_TAG" \
      > "/data/logs/$lname.$i.stdout.log" 2>&1 &
    echo $! > "/tmp/start-sh-$lname.$i.pid"
    cd "$ROOT_DIR"
    log "  $label Worker[$i] 已启动 (pid $!, log: /data/logs/$lname.$i.stdout.log)"
    i=$((i + 1))
  done
}
```

- [ ] **Step 4: 替换 §7 三个 worker 启动块**

把第 290-322 行(从 `# ── 7. RabbitMQ Workers` 注释开始,到 extract worker 的 `fi` 结束)整体替换为:

```bash
# ── 7. RabbitMQ Workers(每类按 WORKER_* 并发补足)────────────
ensure_workers report.worker            "报告解析"  "$WORKER_PARSE"    worker-parsing
ensure_workers interpretation.worker    "解读"      "$WORKER_INTERP"   worker-interpretation
ensure_workers report.extract_worker    "批量解压"  "$WORKER_EXTRACT"   worker-extract
```

> 注意:原 §7 块里 extract worker 的 `cd "$ROOT_DIR"`(322 行前)已被移入 `ensure_workers`,删除整块时一并去掉。

- [ ] **Step 5: 语法与静态检查**

Run: `bash -n start.sh`
Expected: 无输出,退出码 0
Run: `grep -n 'ensure_workers' start.sh`
Expected: 4 处(1 定义 + 3 调用),且所有 `pgrep/pkill` 中 worker 匹配都含 `# $WORKER_TAG`

- [ ] **Step 6: 说明(执行方/用户)手动冒烟(不在本会话执行,避免动生产队列)**

1. 清理本次改动前、本 checkout(`/data/project/hospitalKnowledgeBase/backend`)已运行的无标记旧 worker(仅清本 checkout 的,保留 `/home/wjyy2` 那套):`ps -eo pid,args | grep -E 'app\.modules\.(report|interpretation)\.worker import start_worker' | grep -v '#'` → 对 cwd 为本 checkout 的行 `kill <pid>`。
2. 重启:`bash start.sh`。
3. 核对:`ps -eo pid,args | grep 'app.modules.report.worker import start_worker' | grep -v grep` 应见 2 个 report.worker + 3 个 interpretation.worker + 1 个 extract_worker,且每条 cmdline 尾部带 `# /data/project/hospitalKnowledgeBase/backend`。
4. 队列 consumer 核对(每类应与 worker 数一致):`docker exec hospital-rabbitmq rabbitmqctl list_consumers`(关注 `parsing.bulk` / `interpretation.bulk` / `extract.bulk`)。
5. 发一批上传,对比墙钟与 `/data/logs/app.log` 中 `parsing ok`/`interp ok` 是否多进程交错、无 `file_not_found`/卡 `interpreting`。

Commit: 跳过(用户自行 commit)

---

## Self-Review

**Spec coverage:**
- spec §1(计数原子化)→ Task 1
- spec §2(start.sh 计数补差 + 唯一标记防误杀)→ Task 2
- spec §3(单测 + 手工冒烟)→ Task 1 Step 1/4/5 + Task 2 Step 5/6
- spec「明确不做」全部落入 Global Constraints

**Placeholder scan:** 无 TBD/TODO/「合适处理」类占位;每个代码改动都给出完整 diff 级代码与验证命令。

**Type consistency:** `ensure_workers <module> <label> <count> <logprefix>` 在 Step 3 定义与 Step 4 三处调用参数位置一致;`increment_progress` 签名未变,跨 Task 无同名冲突。Task 2 中 worker cmdline 形态与 Task 2 Step 2 的 pkill 匹配串、Step 3 的 pgrep 匹配串三者一致(`from app.modules.<x> import start_worker; start_worker() # $WORKER_TAG`)。
