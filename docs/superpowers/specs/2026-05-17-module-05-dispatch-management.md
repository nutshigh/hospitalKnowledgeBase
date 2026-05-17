# 模块05：调度管理模块 — 详细设计

## 在整体架构中的位置

```
接入层 → API网关 → 业务模块层
                      ├── 知识库模块
                      ├── 报告解析模块
                      ├── AI解读模块
                      ├── 统计分析模块
                      └── 调度管理模块 ← 本文档
                              ↓
                        基础设施层 → 数据层
```

调度管理模块是系统的横向支撑模块，不直接处理业务数据，而是管理异步任务的排队、优先级、并发控制和系统资源监控，确保高负载下系统稳定运行。

---

## 1. 功能清单

| 编号 | 功能 | 说明 |
|------|------|------|
| D1 | 任务优先级管理 | 支持按紧急程度/批量大小设置任务处理权重 |
| D2 | 弹性算力分配 | 根据实时负载动态调整 AI 模型的并发 Worker 数 |
| D3 | 可视化并发控制台 | 提供系统资源监控大屏（GPU/CPU/内存/队列深度） |
| D4 | 失败任务自动重试 | 失败任务自动重新入队，超限后告警 |
| D5 | 队列深度监控 | 实时监控 RabbitMQ 队列积压，超阈值自动限流或扩容 |
| D6 | 优先级插队 | 紧急/异常报告在任务池中优先处理 |

---

## 2. 模块依赖关系

```
调度管理模块
  │
  ├── 被依赖业务模块:
  │     ├── 报告解析模块（提交解析任务到队列 + 查询任务状态）
  │     └── AI解读模块（提交解读任务到队列）
  │
  ├── 依赖基础设施层:
  │     ├── RabbitMQ（队列管理、消息消费）
  │     └── LLM 网关（GPU 资源监控）
  │
  └── 依赖数据层:
        └── MySQL（任务状态记录、配置存储）
```

| 方向 | 模块/组件 | 依赖内容 |
|------|-----------|----------|
| 被调用 | 报告解析模块 | 提交任务、查询状态 |
| 被调用 | AI 解读模块 | 提交任务、查询状态 |
| 调用 | RabbitMQ | 创建/监控队列、投递消息 |
| 调用 | LLM 网关 | 获取 GPU/推理服务状态 |
| 调用 | MySQL | 读写配置、任务记录 |

---

## 3. 技术栈

| 类别 | 选型 | 用途 |
|------|------|------|
| 后端框架 | FastAPI | 提供并发控制 API 和监控数据接口 |
| 消息队列 | RabbitMQ | 任务队列 + 死信队列 |
| 数据库 | MySQL | 配置存储 |
| 前端可视化 | React + Ant Design + ECharts | 资源监控大屏 |
| 系统监控 | psutil + GPU 驱动 API | 采集 CPU/内存/GPU 指标 |

---

## 4. 数据库设计

### dispatch_config — 调度配置表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| hospital_id | VARCHAR(32) | 所属医院 |
| config_key | VARCHAR(50) | 配置键 |
| config_value | VARCHAR(500) | 配置值 |
| updated_at | DATETIME | 更新时间 |

**配置项：**

| config_key | 说明 | 默认值 |
|------------|------|--------|
| max_parsing_workers | 解析任务并发数 | 4 |
| max_interpretation_workers | 解读任务并发数 | 2 |
| queue_alert_threshold | 队列积压告警阈值 | 100 |
| task_retry_max | 任务最大重试次数 | 3 |
| task_timeout_seconds | 任务超时时间 | 600 |
| rate_limit_qps | 提交速率限制（QPS） | 10 |

### resource_metric — 资源监控快照表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| metric_time | DATETIME | 采集时间 |
| cpu_percent | DECIMAL(5,1) | CPU 使用率 |
| memory_percent | DECIMAL(5,1) | 内存使用率 |
| gpu_percent | DECIMAL(5,1) | GPU 使用率 |
| gpu_memory_percent | DECIMAL(5,1) | GPU 显存使用率 |
| queue_depth | INT | 队列积压数 |
| active_workers | INT | 活跃 Worker 数 |

---

## 5. 处理流程

### 5.1 任务提交与优先级调度

```
1. 业务模块（报告解析/AI解读）提交任务:
   POST /api/v1/dispatch/tasks
   {
     task_type: "parsing" | "interpretation",
     priority: 0 (普通) | 1 (紧急),
     payload: {...}
   }
         ↓
2. 调度管理器:
   - 写入 task 表（MySQL）
   - 根据 hospital_id + task_type + priority 确定 RabbitMQ 路由键
   - 投递到对应队列
         ↓
3. RabbitMQ 队列结构:
   每个 task_type 有 2 个队列:
   - {task_type}.normal   ← 普通优先级
   - {task_type}.urgent   ← 紧急优先级
   Worker 优先消费 urgent 队列
         ↓
4. 返回 task_id 给调用方
```

### 5.2 Worker 并发控制

```
1. Worker 启动时注册到调度管理器
         ↓
2. 调度管理器维护 Worker 连接池，控制活跃 Worker 数:
   - 实际消费数 = min(max_workers, 当前可用)
   - 当队列深度 > queue_alert_threshold:
     → 自动提升 max_workers（不超过 GPU 算力上限）
     → 或触发限流，拒绝新任务提交
         ↓
3. Worker 消费消息 → 执行任务 → ACK
   Worker 异常断开 → 消息重回队列 → 其他 Worker 接管
```

### 5.3 失败重试流程

```
任务执行失败
     ↓
重试次数 < task_retry_max:
     → 消息重新入队（带延迟，如 30s/120s/300s 指数退避）
     → 更新 retry_count + 1
     ↓
重试次数 >= task_retry_max:
     → 消息进入死信队列
     → 标记任务 failed
     → 发送告警
     → 通知用户（通过消息推送）
```

### 5.4 资源监控采集

```
定时采集（每 10 秒）:
  1. psutil: CPU 使用率、内存使用率
  2. GPU API (nvidia-smi 或驱动): GPU 使用率、显存占用
  3. RabbitMQ Management API: 各队列深度、消费速率
         ↓
写入 resource_metric 表
         ↓
前端轮询 GET /api/v1/dispatch/metrics 获取最新数据
  用于实时监控大屏展示
```

---

## 6. RabbitMQ 队列设计

```
交换机: hospital.tasks (topic)

队列:
  1. parsing.urgent      ← 紧急解析任务
  2. parsing.normal      ← 普通解析任务
  3. interpretation.urgent ← 紧急解读任务
  4. interpretation.normal ← 普通解读任务
  5. dead.letter         ← 死信队列（超限失败任务）

路由规则:
  - priority=urgent + type=parsing       → parsing.urgent
  - priority=normal + type=parsing       → parsing.normal
  - priority=urgent + type=interpretation → interpretation.urgent
  - priority=normal + type=interpretation → interpretation.normal

消费策略:
  - 每个 Worker 同时监听 urgent + normal 队列
  - 优先拉取 urgent 队列消息
  - 手动 ACK，处理完成后确认
```

---

## 7. API 接口设计

```
# 任务提交（内部调用）
POST /api/v1/dispatch/tasks           — 提交任务到队列

# 任务状态查询
GET  /api/v1/dispatch/tasks/{task_id} — 查询任务状态

# 并发控制（医生端管理）
GET    /api/v1/dispatch/config         — 获取当前调度配置
PUT    /api/v1/dispatch/config         — 修改调度配置（max_workers 等）

# 资源监控（医生端 + 管理后台）
GET /api/v1/dispatch/metrics/current   — 获取当前系统资源快照
GET /api/v1/dispatch/metrics/history   — 获取历史监控数据（传入时间范围）

# 队列状态
GET /api/v1/dispatch/queues            — 获取各队列深度、消费速率
```

---

## 8. 前端监控大屏

```
┌─────────────────────────────────────────────────────┐
│  系统资源监控大屏                   刷新: 10s          │
├────────────────┬────────────────┬───────────────────┤
│  CPU 使用率     │  内存使用率     │  GPU 使用率        │
│  [进度环 45%]   │  [进度环 62%]   │  [进度环 78%]      │
├────────────────┴────────────────┴───────────────────┤
│  GPU 显存占用       │ 活跃 Worker  │  队列积压         │
│  [进度条 8/16GB]    │     3/4       │  parsing: 12     │
│                     │               │  interp: 8      │
├──────────────────────────────────────────────────────┤
│  队列积压趋势 (折线图，近 30 分钟)                      │
│                                                      │
│  ─ parsing  ─ interpretation                         │
├──────────────────────────────────────────────────────┤
│  任务统计 (今日)                                       │
│  解析完成: 234  |  失败: 5  |  平均耗时: 12s           │
│  解读完成: 230  |  失败: 2  |  平均耗时: 8s            │
└──────────────────────────────────────────────────────┘
```

---

## 9. 错误处理

| 场景 | 处理方式 |
|------|----------|
| RabbitMQ 连接断开 | Worker 自动重连，重连期间消息持久化不丢失 |
| Worker 崩溃 | 未 ACK 的消息自动重回队列，被其他 Worker 接管 |
| 队列积压超阈值 | 自动扩容 Worker 或触发限流，同时告警 |
| GPU 资源耗尽 | 暂停新解读任务入队，待 GPU 释放后恢复 |
| 死信队列堆积 | 告警通知管理员人工介入 |
