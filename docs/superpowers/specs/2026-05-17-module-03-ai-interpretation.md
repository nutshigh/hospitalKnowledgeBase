# 模块03：AI 解读模块 — 详细设计

## 在整体架构中的位置

```
接入层 → API网关 → 业务模块层
                      ├── 知识库模块
                      ├── 报告解析模块
                      ├── AI解读模块 ← 本文档
                      ├── 统计分析模块
                      └── 调度管理模块
                              ↓
                        基础设施层 → 数据层
```

AI 解读模块是系统的核心大脑，消费报告解析模块产出的结构化数据，结合知识库检索和 LLM 推理，生成指标研判、三色分级和健康建议。

---

## 1. 功能清单

| 编号 | 功能 | 说明 |
|------|------|------|
| I1 | 全量指标智能研判 | 对报告中每个指标判定：正常、偏高、偏低、危急值 |
| I2 | 三色分级预警 | 基于规则引擎输出红（严重异常）/黄（轻度异常或潜在风险）/绿（正常）标签 |
| I3 | 健康解读生成 | 结合知识库检索结果 + LLM，生成异常指标解读及健康建议 |
| I4 | 历年对比研判 | 同一指标跨年度对比，判断趋势变化（改善/稳定/恶化） |
| I5 | 报告综合评估 | 汇总单份报告所有指标，输出整体评级和红区指标清单 |
| I6 | 高风险人群汇总 | 按医院/单位汇总红区人员名单，生成预警列表 |
| I7 | 复查/干预建议 | 对红区指标生成复查通知或紧急干预建议报告 |

---

## 2. 模块依赖关系

```
AI解读模块
  │
  ├── 触发源: 报告解析模块（消费解析完成事件）
  │
  ├── 依赖业务模块:
  │     └── 知识库模块（调用检索接口获取医学上下文）
  │
  ├── 依赖基础设施层:
  │     ├── RabbitMQ（消费解析完成事件 + 发布解读完成事件）
  │     └── LLM 网关（调用本地部署的 LLM 生成解读）
  │
  └── 依赖数据层:
        └── MySQL（读取结构化报告、写入解读结果和分级标签）
```

| 方向 | 模块/组件 | 依赖内容 |
|------|-----------|----------|
| 消费事件 | 报告解析模块 | RabbitMQ "report_parsed" 事件 |
| 调用 | 知识库模块 | POST /api/v1/knowledge/search 检索医学知识 |
| 调用 | LLM 网关 | 提交带知识上下文的 Prompt，获取解读文本 |
| 调用 | MySQL | 读取报告数据、写入解读结果、查询历年数据 |
| 发布事件 | RabbitMQ | 解读完成后发布"interpretation_done"事件 |

---

## 3. 技术栈

| 类别 | 选型 | 用途 |
|------|------|------|
| 后端框架 | FastAPI | 提供解读查询和统计 API |
| 数据库 | MySQL | 存储解读结果、分级标签 |
| 消息队列 | RabbitMQ | 消费解析事件 + 发布解读完成事件 |
| LLM | 本地私有化部署（如 Qwen/DeepSeek 开源模型） | 生成解读文本 |
| 规则引擎 | 自研（Python） | 指标判定 + 三色分级（确定性逻辑） |

---

## 4. 数据库设计

### report_interpretation — 解读结果表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| report_id | BIGINT FK | 关联 report_info.id |
| hospital_id | VARCHAR(32) | 所属医院 |
| overall_level | VARCHAR(10) | 报告整体评级：red / yellow / green |
| red_count | INT | 红区指标数 |
| yellow_count | INT | 黄区指标数 |
| green_count | INT | 绿区指标数 |
| summary_text | TEXT | 报告综合小结（LLM 生成） |
| status | VARCHAR(20) | pending / processing / completed / failed |
| retry_count | INT | 重试次数 |
| created_at | DATETIME | 创建时间 |
| completed_at | DATETIME | 完成时间 |

### indicator_judgment — 指标研判明细表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| interpretation_id | BIGINT FK | 关联 report_interpretation.id |
| indicator_id | BIGINT FK | 关联 report_indicator.id |
| item_name | VARCHAR(100) | 指标名称 |
| result_value | VARCHAR(50) | 结果值 |
| deviation | VARCHAR(10) | 偏离方向：normal / high / low / critical |
| color_level | VARCHAR(10) | red / yellow / green |
| matched_rule_id | BIGINT | 命中的规则 ID |
| explanation | TEXT | LLM 生成的指标解读 |
| suggestion | TEXT | LLM 生成的健康建议 |
| knowledge_refs | JSON | 引用的知识条目 [{entry_id, title}] |

### triage_rule — 三色规则表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| hospital_id | VARCHAR(32) | 所属医院 |
| rule_name | VARCHAR(100) | 规则名称 |
| rule_type | VARCHAR(20) | value_range / key_indicator / combo / trend |
| indicator_code | VARCHAR(50) | 适用的指标编码（可为空表示通用） |
| conditions | JSON | 规则条件（阈值、逻辑组合） |
| color_level | VARCHAR(10) | 满足条件时输出的等级 |
| priority | INT | 执行优先级 |
| is_active | TINYINT | 是否启用 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

**conditions JSON 示例：**

```json
// 数值范围规则
{ "field": "result_value", "op": "gte", "value": 33.3, "unit": "mmol/L" }

// 组合规则
{ "logic": "AND", "rules": [
    { "indicator": "ALT", "op": "gt", "multiplier": 2 },
    { "indicator": "AST", "op": "gt", "multiplier": 2 }
]}

// 趋势规则
{ "field": "trend", "op": "eq", "value": "continuous_worsening", "years": 3 }
```

---

## 5. 处理流程

### 5.1 解读主流程

```
1. 消费 RabbitMQ 事件: {event: "report_parsed", report_id, hospital_id}
         ↓
2. 创建 report_interpretation 记录，status = processing
         ↓
3. 从 MySQL 加载结构化报告数据（report_info + report_indicator）
         ↓
4. 逐指标进入规则引擎研判:
   ┌─────────────────────────┐
   │  Step 1: 数值范围规则     │
   │  结果值 vs 参考区间        │
   │  正常 → green            │
   │  轻度偏离 → yellow         │
   │  显著偏离 → red           │
   │  命中危急值 → 强制 red     │
   ├─────────────────────────┤
   │  Step 2: 关键指标规则     │
   │  肿瘤标志物等关键指标异常   │
   │  → 直接 red              │
   ├─────────────────────────┤
   │  Step 3: 组合规则         │
   │  多指标联合判断           │
   │  → 对应等级               │
   ├─────────────────────────┤
   │  Step 4: 趋势规则         │
   │  查询历年同指标数据         │
   │  持续恶化 → 升级等级       │
   └─────────────────────────┘
         ↓
5. 汇总异常指标列表，构建检索 Query
         ↓
6. 调用知识库模块检索接口:
   POST /api/v1/knowledge/search
   获取 Top-K 医学知识上下文
         ↓
7. 构建 LLM Prompt:
   - 报告数据（指标名 + 结果 + 参考区间）
   - 规则引擎判定结果（标签 + 偏离方向）
   - 知识库检索结果（医学知识上下文）
   - 历年对比数据（如有）
         ↓
8. 调用 LLM 生成解读:
   - 每项异常指标的解读 + 健康建议
   - 引用知识来源
   - 报告综合小结
         ↓
9. 写入数据库:
   - indicator_judgment: 逐项结果
   - report_interpretation: 更新 status = completed
         ↓
10. 判断是否红区: 有 red 指标 → 加入高风险人群汇总表
          ↓
11. 发布 RabbitMQ 事件: {event: "interpretation_done", report_id}
    （供统计分析模块和通知服务消费）
```

### 5.2 LLM Prompt 结构

```
System:
你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，
为体检者撰写易懂的指标解读和健康建议。

规则:
1. 绿区指标一笔带过，重点解读红区和黄区
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统的"注意饮食"
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"

User:
## 体检者信息
{name}, {gender}, {age}岁

## 本次报告数据
| 指标 | 结果 | 参考区间 | 判定 |
|------|------|----------|------|
| 空腹血糖 | 8.2 | 3.9-6.1 | 偏高(红) |
| 总胆固醇 | 5.8 | 3.0-5.7 | 偏高(黄) |

## 历年对比
（如有）

## 参考知识库
{检索结果：标题 + 内容 + 来源}

请逐个解读异常指标，给出健康建议，并生成综合小结。
```

### 5.3 历年对比逻辑

```
1. 提取当前报告中的所有指标
2. 按 user_id + item_name_standard 查询历史报告中的同指标数据
3. 按报告日期排序，取近 3 年数据
4. 计算趋势:
   - 改善: 偏离值逐年减小
   - 稳定: 偏离值波动在阈值内
   - 恶化: 偏离值逐年增大
5. 恶化趋势 → 触发趋势规则，升级颜色等级
```

---

## 6. 规则引擎详细设计

### 6.1 规则加载

系统启动时从 triage_rule 表加载所有规则，按 hospital_id 分组缓存。
医生端修改规则后，触发缓存局部刷新。

### 6.2 规则执行顺序

```
priority 1: 危急值规则 → 直接标红，跳过后续规则
priority 2: 关键指标规则 → 直接标对应等级
priority 3: 数值范围规则 → 计算偏离度判定
priority 4: 组合规则 → 多指标联合
priority 5: 趋势规则 → 可能升级等级
```

### 6.3 等级升级原则

- 多个规则命中同一指标，取最高等级
- 趋势规则可以将 green→yellow 或 yellow→red，但不能降级
- 危急值规则一票否决，不可被降级

---

## 7. API 接口设计

```
GET  /api/v1/interpretations/{report_id}          — 获取报告解读结果
GET  /api/v1/interpretations/high-risk             — 高风险人群列表（医生端，按医院筛选）
POST /api/v1/interpretations/high-risk/{user_id}/recheck — 一键下发复查通知
GET  /api/v1/interpretations/{report_id}/indicators — 获取单份报告的全部指标研判

# 规则管理（医生端）
GET    /api/v1/triage-rules                       — 规则列表
POST   /api/v1/triage-rules                       — 创建规则
PUT    /api/v1/triage-rules/{id}                  — 编辑规则
DELETE /api/v1/triage-rules/{id}                  — 删除规则
```

---

## 8. 错误处理

| 场景 | 处理方式 |
|------|----------|
| LLM 调用超时 | 重试 3 次，仍失败标记 interpretation 为 failed |
| 知识库检索无结果 | 不带知识上下文调用 LLM，日志记录"知识缺失" |
| 规则引擎计算异常 | 默认标记为 yellow，人工审核后再定 |
| 历年数据查询失败 | 跳过趋势研判，仅用当前数据判定 |
| 消息消费失败 | RabbitMQ ack 机制保证至少一次消费 |
