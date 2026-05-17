# 模块04：统计分析模块 — 详细设计

## 在整体架构中的位置

```
接入层 → API网关 → 业务模块层
                      ├── 知识库模块
                      ├── 报告解析模块
                      ├── AI解读模块
                      ├── 统计分析模块 ← 本文档
                      └── 调度管理模块
                              ↓
                        基础设施层 → 数据层
```

统计分析模块是数据消费层，基于解读完成的结构化数据，提供多维度统计、趋势分析、BI 看板和自动化报表导出功能。

---

## 1. 功能清单

| 编号 | 功能 | 说明 |
|------|------|------|
| S1 | 健康画像生成 | 按单位/部门生成疾病谱、前 N 位高发疾病分布图 |
| S2 | 多维数据交叉对比 | 按性别、年龄、单位等维度灵活筛选与交叉比对 |
| S3 | 变化趋势分析 | 慢性病、重大疾病年度发病率变化趋势曲线 |
| S4 | BI 分析看板 | 可视化可交互的数据分析看板 |
| S5 | 自动化报表导出 | 基于模版一键生成 Word/Excel/PDF 格式报告 |

---

## 2. 模块依赖关系

```
统计分析模块
  │
  ├── 直接消费数据库（不依赖其他业务模块）
  │
  ├── 依赖数据层:
  │     └── MySQL（跨医院聚合查询解读结果 + 指标数据）
  │
  └── 被调用者: 医生端（查看统计分析、导出报表）
```

| 方向 | 模块/组件 | 依赖内容 |
|------|-----------|----------|
| 消费 | MySQL | 读取 report_info、report_indicator、indicator_judgment 数据 |
| 被调用 | 医生端 | 统计 API、报表导出 |

统计分析模块独立性最强——不依赖其他业务模块，不依赖消息队列，只做只读查询和报表生成。

---

## 3. 技术栈

| 类别 | 选型 | 用途 |
|------|------|------|
| 后端框架 | FastAPI | 提供统计查询和报表导出 API |
| 数据库 | MySQL | 聚合查询源数据 |
| 图表生成 | ECharts（前端）/ Matplotlib（后端导出） | 前端可视化 + 后端报表图表渲染 |
| 报表生成 | python-docx / openpyxl / WeasyPrint | Word / Excel / PDF 报表生成 |
| 缓存 | Redis（建议） | 统计结果缓存，减少重复计算 |

---

## 4. 数据库设计

统计分析模块不新增核心数据表，主要基于已有数据做聚合查询。新增以下辅助表：

### report_template — 报表模板表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| hospital_id | VARCHAR(32) | 所属医院 |
| name | VARCHAR(100) | 模板名称 |
| type | VARCHAR(10) | word / excel / pdf |
| content | LONGBLOB | 模板文件内容 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### statistic_cache — 统计结果缓存表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| hospital_id | VARCHAR(32) | 所属医院 |
| stat_type | VARCHAR(50) | 统计类型标识 |
| params_hash | VARCHAR(64) | 查询参数哈希 |
| result_json | JSON | 缓存结果 |
| expired_at | DATETIME | 过期时间 |

---

## 5. 处理流程

### 5.1 健康画像生成流程

```
1. 医生端选择:
   - 医院
   - 单位/部门（可选，不选=全部）
   - 时间范围（默认当年）
         ↓
2. 查询聚合:
   SELECT item_name_standard, color_level, COUNT(*) as cnt
   FROM indicator_judgment ij
   JOIN report_info ri ON ij.report_id = ri.id
   WHERE ri.hospital_id = {hospital_id}
     AND ri.report_date BETWEEN {start} AND {end}
     AND color_level IN ('red', 'yellow')
   GROUP BY item_name_standard, color_level
   ORDER BY cnt DESC
         ↓
3. 构建健康画像数据:
   - 疾病谱: 异常指标分布 Top 10
   - 高发疾病: 红区指标排行前 5
   - 按单位对比（如选择全部）
         ↓
4. 渲染为图表数据返回前端（ECharts 配置 JSON）
```

### 5.2 多维度交叉对比流程

```
1. 医生端选择:
   - 对比维度（X轴: 单位/性别/年龄段）
   - 对比指标（Y轴: 异常率/某项指标均值）
   - 时间范围
         ↓
2. 按维度分组聚合:
   X轴维度（GROUP BY）+ 指标（COUNT/AVG）+ 筛选条件
         ↓
3. 返回交叉对比数据 + 图表配置
```

### 5.3 趋势分析流程

```
1. 医生端选择:
   - 疾病/指标（如: 空腹血糖异常率）
   - 时间跨度（如: 近 5 年）
         ↓
2. 按年度聚合:
   SELECT YEAR(report_date) as year,
          COUNT(CASE WHEN color_level='red' THEN 1 END) as red_cnt,
          COUNT(*) as total_cnt
   ... GROUP BY year
         ↓
3. 计算每年异常率 → 生成趋势曲线数据
```

### 5.4 报表导出流程

```
1. 医生端选择:
   - 报表类型（Word/Excel/PDF）
   - 报表模板
   - 数据范围
         ↓
2. 异步任务创建:
   - 写入导出任务表
   - 入 RabbitMQ 队列
         ↓
3. Worker 处理:
   - 加载模板
   - 按查询条件聚合数据
   - 填充模板 + 插入图表（Matplotlib 渲染为图片）
   - 生成最终文件
         ↓
4. 返回下载链接，通知用户
```

---

## 6. API 接口设计

```
# 健康画像
GET /api/v1/statistics/health-profile
    参数: hospital_id, unit_name(可选), start_date, end_date

# 多维对比
GET /api/v1/statistics/cross-compare
    参数: hospital_id, x_dimension(gender/age/unit), y_metric, start_date, end_date

# 趋势分析
GET /api/v1/statistics/trend
    参数: hospital_id, indicator, years, start_date, end_date

# BI 看板概览
GET /api/v1/statistics/dashboard
    参数: hospital_id, start_date, end_date
    返回: 综合统计数据（总报告数、异常率、红区人数、高发排行）

# 报表导出
POST /api/v1/statistics/export
    请求: {hospital_id, template_id, type: word/excel/pdf, start_date, end_date}
    返回: {task_id}

GET  /api/v1/statistics/export/{task_id}
    返回: {status, download_url}
```

---

## 7. 跨院统计（平台管理后台）

平台超管需要查看跨医院汇总数据。处理方式：

```
1. 遍历平台所有活跃医院
2. 对每家医院数据库分别执行查询
3. 汇总结果（可并行查询）
4. 返回合并后的统计数据
```

对于跨院统计查询，不做实时大范围聚合，通过定时任务预计算 + 缓存方案。

---

## 8. 错误处理

| 场景 | 处理方式 |
|------|----------|
| 查询时间范围过大导致慢查询 | 限制最大查询跨度（如 5 年），超出提示缩小范围 |
| 报表导出模板缺失 | 检查后返回 400 提示"模板不存在" |
| 报表导出生成失败 | 记录失败日志，通知用户重新发起导出 |
| 跨院查询某医院库连接失败 | 跳过该医院，结果中标注"数据不完整" |
