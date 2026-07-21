# 团体健康体检分析报告 — 设计

> Date: 2026-07-21
> Status: Approved (design)
> Owner: AI Agent

## 1. 背景与动机

后台管理人员希望按维度(单位/批次/时间/年龄/性别)生成**团体**健康体检分析数据,支持图表展示与重点人群清单查看。

经与用户澄清:

- **"单位"在本项目语义里即指"医院",每个 tenant 就是一家医院**(`hospital_{id}`)。因此按单位维度 = 跨院维度,本设计为**平台级跨院分析**而非单院内分析。
- "后台管理人员" = 平台 admin,鉴权走 **JWT + `require_role("admin")`**(复用 `app/core/dependencies.py:39-43` 的 `require_role` 工厂),与 admin-portal 现有 `/auth/login` 流程(`api/auth.py:34-54`)一致。平台 admin 的 `platform_user` 行 `role='admin'` 且 `hospital_id` 可空;`get_current_user`(`dependencies.py:19-36`)注入 `CurrentUser(role='admin', hospital_id=None)` 后,本设计的跨院聚合 service 显式调 `get_all_hospital_ids()` 跨库,不读 ContextVar 单院上下文。**不**使用 `X-Admin-Token`(那是 `tenant/router.py` 专属的 bootstrap 共享密钥模式,不适合 admin-portal 已实现的 JWT 登录流)。
- 已有 `statistics` 模块(`backend/app/modules/statistics/`,5 个 endpoint + doctor-portal 现有 5 页但无图表)有部分重合,本设计**扩展该模块**而非新建模块或废弃现有功能。
- 现有 `statistic_cache` 表预留但未使用 —— 本设计**不接缓存(YAGNI)**,按需计算;数据量真上去再启。
- **不动解析链**:`report_info.unit_name` 在跨院语义里由 `hospital_id` 取代,无需重启用 `report/service.py:117-118` 被注释的单位写入。

## 2. 现状关键事实

- `backend/app/modules/statistics/router.py`(无鉴权,用 ContextVar 读 hospital_id)+ `service.py`(已实现 dashboard / health-profile / cross-compare / trend / export stub)。
- `statistics/service.py:122` 已有 `cross_hospital_summary(dbs)` 雏形 —— 遍历多 tenant session 做跨院汇总的先例。
- `backend/app/core/database.py:58-67` 的 `get_all_hospital_ids()` 列活跃 tenant;`get_hospital_db(hid)`(`database.py:49-55`)按 hospital_id 开 session,引擎池复用。
- 数据来源表(per-tenant):
  - `report_info`(`report/models.py:24-37`):`task_id` / `age` / `gender` / `report_date` / `unit_name`(**不用**)
  - `report_interpretation`(`interpretation/models.py:5-22`):`overall_level`(red/yellow/green)、`red_count` / `yellow_count` / `green_count` / `summary_text`
  - `indicator_judgment`(`interpretation/models.py:25-40`):`item_name` / `color_level`(red/yellow/green)
  - `batch_import`(per-tenant 库,`report/batch_models.py:5-37`):`id` / `filename` / `created_at`
  - 链路:`report_info.task_id` → `report_task.id` ⇄ `batch_import_file.report_task_id` → `batch_import.id`(`report_task` 上无 `batch_id` 列,需多跳 JOIN)
- 平台 admin 鉴权模式:JWT + `require_role("admin")`(复用 `app/core/dependencies.py:39-43`);`platform_user.role='admin'` 且 `hospital_id` 可空表示平台级(非绑某院)。`tenant/router.py` 的 `X-Admin-Token` 共享密钥模式仅用于 tenant 创建 bootstrap,不复用。
- 前端 `frontend/packages/admin-portal`(端口 3003)目前仅 Login + PlatformDashboard 两页,鉴权 `adminStore.token` 即 `/auth/login` 返回的 JWT 字符串(`stores/adminStore.ts:9-13`啜 `setAuth`),shared `apiClient`(`frontend/packages/shared/src/api/client.ts:6-15`)的请求拦截器把它放进 `Authorization: Bearer ...`。**无任何图表库**(`package.json` grep `echarts/recharts/chart` 0 命中)。
- AGENTS.md 多 tenant 表初始化提示:新设计若不动 DDL 本身,新增 tenant 走 `start.sh` 原 DDL 块即可,无新负担。

## 3. 决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 模块归属 | 扩展 `backend/app/modules/statistics/` 新增 `group` 子命名空间 | 复用现有聚合 SQL 思路、`cross_hospital_summary` 模式、statistic_cache 表(备用);减少跨包散落 |
| 鉴权 | JWT + `require_role("admin")`(复用 `app/core/dependencies.py:39-43`),admin-portal 现有 `/auth/login` 直接喂 JWT | 与 admin-portal 现有 JWT 登录流一致,前端复用 shared `apiClient` 不需新增 header 通道;`X-Admin-Token` 留给 tenant router 不混用 |
| 跨库聚合 | 后端遍历 `get_all_hospital_ids()`,每库开 `Session` 跑聚合 SQL,Python 层 merge | 无跨库 JOIN 能力;`cross_hospital_summary` 已走此路 |
| 并发 | `ThreadPoolExecutor(max_workers=8)`,单库 SQL 超时 5s | 控制总响应时延 < 10s;慢库不阻塞其它 |
| 单库失败 | catch 异常 → 该 tenant 进 `rows` 标 `error:"db_unavailable"` 并继续,HTTP 200 | 部分库故障不应整体失败,前端能在卡片上标记 |
| 数据维度 | 医院 / 批次 / 年龄段 / 性别 / 时间月 | 用户给定 5 维;均能用现有列直接推 |
| 指标口径 | 体检人数 + 红/黄/绿三色人数 + 异常率 + Top10 异常指标(仅 hospital/batch 维度附) + 性别/年龄 sub-distribution(仅 hospital 维度附) | 标准套餐,不引入指标分类映射等扩展 |
| 重点人群判定 | `report_interpretation.overall_level='red'` OR `red_count>=3` | 用现有列,不需新字段或迁移 |
| 缓存 | **不引入** `statistic_cache` | YAGNI;按需计算;出现慢查询再启 |
| 前端宿主 | `admin-portal` 新增 `/group-analysis` 页 | 平台 admin 入口;doctor-portal 角色语义不符 |
| 图表库 | `echarts` + `echarts-for-react`(tree-shake 按 import) | 已选 ECharts;后续按需 import 控制包大小 |
| 重点人群导出 | 后端同接口 `?format=csv` 输出 UTF-8 BOM CSV,前端 `<a download>` | 避免前端大表;Excel 包重 YAGNI |
| 解析链 | 不改 | unit_name 语义由 hospital_id 取代 |
| DDL | 不改 | 跨院表无需新表/新列 |

## 4. 数据流

```
admin-portal (/group-analysis 页, ECharts)
   │  GET /api/v1/statistics/group/overview?group_by=...&filters=...
   │  GET /api/v1/statistics/group/high-risk?...&page=1&page_size=20
   │  GET /api/v1/statistics/group/high-risk?...&format=csv
   ▼
statistics/group_router.py   (新;get_current_user + require_role('admin') dependency)
   ▼
statistics/group_service.py  (新;纯函数,不持久化)
   │  1. get_all_hospital_ids() → 活跃 tenant 列表 + batch_ids 反查所属 tenant
   │  2. 应用 hospital_ids 过滤
   │  3. ThreadPool(max_workers=8) 并发遍历 tenant
   │       每库 get_hospital_db(hid).execute(group_sql.py 按维度生成的 text())
   │       单库失败 catch → 该 tenant row 标 error,继续
   │  4. 把各库结果 merge 成统一结构,加 hospital_id/hospital_name
   │  5. 跨 row 汇总 totals
   ▼
statistics/group_sql.py  (新;按 group_by 与 filters 生成 per-tenant text() SQL)
              ↑ JOIN report_info e2 + report_interpretation
                + indicator_judgment(用于 Top items 与 red_count filter)')
                + batch_import_file/report_task/batch_import(仅 batch 维度)
```

## 5. API 契约

设计沿用 `batch_router.py` 风格:**无信封**,直接返回 dict,出错抛 `HTTPException`。鉴权失败给 403,验证错误给 422(Pydantic 自动)。

### 5.1 `GET /api/v1/statistics/group/overview`

Header:
```
Authorization: Bearer <JWT>   (admin-portal /auth/login 返回的 access_token; role 必须 == "admin")
```

Query:
| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `group_by` | enum{hospital, batch, age_group, gender, time_month} | 必填 | 分组维度 |
| `hospital_ids` | csv | 全活跃 | 平台级医院过滤 |
| `batch_ids` | csv(`uuid`)| — | 仅属于 hospital_ids 子集时生效;`group_by=batch` 时直接作为分组键 |
| `date_from` | ISO date | — | `report_info.report_date >=` |
| `date_to` | ISO date | — | `report_info.report_date <=` |
| `gender` | enum{M, F} | — | 性别过滤 |
| `age_groups` | csv(`<20`,`20-29`,`30-39`,`40-49`,`50-59`,`60+`) | 全 | 年龄段过滤 |
| `topn` | int | 10 | Top 异常指标数(仅 hospital/batch 维度附) |

Response 200:
```json
{
  "group_by": "hospital",
  "filters": {
    "hospital_ids": ["H001","H002"], "batch_ids": null,
    "date_from": "2026-01-01", "date_to": "2026-06-30",
    "gender": null, "age_groups": null, "topn": 10
  },
  "rows": [
    {
      "key": "H001",
      "label": "杭州第一医院",
      "total_people": 1234,
      "red_count": 101, "yellow_count": 230, "green_count": 903,
      "abnormal_rate": 0.268,
      "by_gender": [{"key":"M","count":620}, {"key":"F","count":614}],
      "by_age_group": [
        {"key":"<20","count":12}, {"key":"20-29","count":140},
        {"key":"30-39","count":300}, {"key":"40-49","count":420},
        {"key":"50-59","count":260}, {"key":"60+","count":102}
      ],
      "top_abnormal_items": [
        {"item":"BMI","red_count":80},
        {"item":"血压偏高","red_count":45}
      ]
    },
    {
      "key": "H003",
      "label": "三家坏库示例",
      "error": "db_unavailable"
    }
  ],
  "totals": {
    "total_people": 2468,
    "red_count": 200, "yellow_count": 460, "green_count": 1808,
    "abnormal_rate": 0.268
  }
}
```

- `by_gender` / `by_age_group` 仅 `group_by=hospital` 的 rows 附;
- `top_abnormal_items` 仅 `group_by ∈ {hospital, batch}` 的 rows 附;
- 其它 `group_by` 的 rows 仅含核心 5 指标(`total_people` / `red_count` / `yellow_count` / `green_count` / `abnormal_rate`)。

`key`/`label` 按 `group_by` 推:
- `hospital`:`key=hospital_id`,`label=hospital_name`
- `batch`:`key=batch_id(uuid)`,`label=batch_import.filename`
- `age_group`:`key=age_group 段名`(如 `30-39`)
- `gender`:`key=M/F`
- `time_month`:`key=YYYY-MM`

### 5.2 `GET /api/v1/statistics/group/high-risk`

Query:**同 5.1 的 filters**(`hospital_ids` / `batch_ids` / `date_from` / `date_to` / `gender` / `age_groups`,**不**含 `group_by`/`topn`),另加:

| 参数 | 说明 |
|---|---|
| `sort` | enum{red_count, age, report_date} default=`red_count` desc |
| `page` | int ge=1 default=1 |
| `page_size` | int ge=1 le=200 default=20 |
| `format` | enum{json, csv} default=`json` |

Response (`format=json`,200):
```json
{
  "items": [
    {
      "hospital_id": "H001",
      "hospital_name": "杭州第一医院",
      "report_id": 12,
      "user_id": 34,
      "name": "张三",
      "gender": "M",
      "age": 45,
      "report_date": "2026-03-12",
      "batch_id": "...uuid or null...",
      "batch_name": "2026Q1入职体检.zip or null",
      "overall_level": "red",
      "red_count": 5,
      "yellow_count": 2,
      "summary_text": "..."
    }
  ],
  "total": 233,
  "page": 1,
  "page_size": 20,
  "filters": { ... echo ... }
}
```

Response (`format=csv`,200):
- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="high-risk-YYYYMMDD.csv"`
- UTF-8 BOM(`\ufeff`)+ 表头列同 `items` 字段顺序
- 单次最大 rows 50000;超过返回 413 `{"detail":"high-risk export exceeds 50000 rows, please narrow filters","code":"PAYLOAD_TOO_LARGE"}`

### 5.3 错误

| 状态 | 触发 |
|---|---|
| 401 | JWT 缺失 / 过期 / 解码失败(`UnauthorizedException`,见 `dependencies.py:24-29`) |
| 403 | JWT 有效但 `role != "admin"`(`ForbiddenException`,见 `dependencies.py:39-43` 的 `require_role("admin")`) |
| 422 | `group_by` 不在枚举 / `page<1` / `page_size>200` / ISO 日期格式错 |
| 400 | `date_from > date_to` |
| 413 | CSV 导出行数 > 50000 |
| 422 | `batch_ids` 中 `hospital_ids` 限制使其 prefix 不属于任何当前 tenant(忽略未知,不报 422 —— 与"未知 id 忽略"策略一致)|
| 500 | 总体异常被 global handler 兜底,返回 `INTERNAL_ERROR`;单库失败不抛 500 |

## 6. 后端模块改动

### 6.1 文件结构

```
backend/app/modules/statistics/
├── router.py            (existing;不动现有 5 endpoint)
├── group_router.py      (新;2 group endpoints)
├── group_service.py     (新;核心聚合,ThreadPool 并发)
├── group_sql.py         (新;SQL 生成 helper,返回 text()+params)
├── group_schemas.py     (新;Pydantic 枚举/Filter/Row/Item 模型)
└── service.py           (existing;不动)
```

`backend/app/main.py` 注册(在现有 statistics include 之后):
```python
from app.modules.statistics.group_router import router as statistics_group_router
...
app.include_router(statistics_group_router, prefix="/api/v1/statistics", tags=["statistics"])
```

### 6.2 鉴权依赖

不新建依赖文件。`group_router.py` 直接复用 `app/core/dependencies.py:39-43` 的 `require_role("admin")`:

```python
from app.core.dependencies import require_role

@router.get("/group/overview")
def group_overview(
    _admin: None = Depends(require_role("admin")),
    group_by: GroupBy = Query(...),
    ...
):
    ...
```

`get_current_user`(`dependencies.py:19-36`)注入 `CurrentUser(role, hospital_id)`,平台 admin 行 `role='admin'` 且 `hospital_id=None`;本设计的 service 显式调 `get_all_hospital_ids()` 跨库,不读 ContextVar `current_hospital_id`(平台 admin 也不应该 ContextVar 单院上下文)。tenant router 仍保留其独立的 X-Admin-Token 模式,不重构(避免影响 tenant 创建接口现状)。

### 6.3 关键 SQL 组合(在 `group_sql.py` 内)

**核心公共片段**(每库):
```sql
SELECT
  COUNT(DISTINCT ri.id) AS total_people,
  SUM(CASE WHEN interp.overall_level='red'    THEN 1 ELSE 0 END) AS red_count,
  SUM(CASE WHEN interp.overall_level='yellow' THEN 1 ELSE 0 END) AS yellow_count,
  SUM(CASE WHEN interp.overall_level='green'  THEN 1 ELSE 0 END) AS green_count
FROM report_info ri
JOIN report_interpretation interp ON interp.report_id = ri.id
WHERE interp.status='completed'
  [AND ri.report_date BETWEEN :date_from AND :date_to]
  [AND ri.gender = :gender]
  [AND <age_group CASE 表达式>]
  [AND EXISTS (batch 链路 match :batch_ids 当 batch_ids 非空时)]
```

`group_by` 决定 SELECT 附加 GROUP BY 列:
- `hospital`:无附加(GROUP BY 由 Python 跨库组装),仅返回该库一行;`by_gender`/`by_age_group` 在同库内多跑 2 个辅助 query
- `batch`:附加 `bi.id` + LEFT JOIN 链路 + GROUP BY `bi.id`
- `age_group`:附加 `CASE WHEN ri.age<20 THEN '<20' ... END AS age_group` + GROUP BY
- `gender`:附加 `ri.gender` + GROUP BY
- `time_month`:附加 `DATE_FORMAT(ri.report_date,'%Y-%m')` + GROUP BY

`top_abnormal_items`(仅 hospital/batch):额外 ```SELECT ij.item_name, SUM(CASE WHEN ij.color_level='red' THEN 1 ELSE 0 END) FROM indicator_judgment ij JOIN report_interpretation interp ON interp.id = ij.interpretation_id WHERE interp.status='completed' AND ij.color_level='red' [+filters] GROUP BY ij.item_name ORDER BY 2 DESC LIMIT :topn```

**年龄段 CASE**:
```sql
CASE
  WHEN ri.age < 20 THEN '<20'
  WHEN ri.age BETWEEN 20 AND 29 THEN '20-29'
  WHEN ri.age BETWEEN 30 AND 39 THEN '30-39'
  WHEN ri.age BETWEEN 40 AND 49 THEN '40-49'
  WHEN ri.age BETWEEN 50 AND 59 THEN '50-59'
  WHEN ri.age >= 60 THEN '60+'
END
```

**batch 链路 JOIN**:
```sql
LEFT JOIN batch_import_file bf ON bf.report_task_id = ri.task_id
LEFT JOIN batch_import b       ON b.id = bf.batch_id
```
(`report_task` 上有 `id`,`report_info.task_id` 即 `report_task.id`,直接两跳到 batch。本期 JOIN 简化为无 `report_task` 中转,因 `batch_import_file.report_task_id` 已是该 task_id;若 DDL 确证 `report_info.task_id` 直接 = `report_task.id` 而非外键,实现时按 `models.py` 实际列名调整。)

**重点人群 list 单库 SQL**(`format=json`,带分页):
```sql
SELECT ri.id AS report_id, hu.id AS user_id, hu.name, ri.gender, ri.age,
       ri.report_date, b.id AS batch_id, b.filename AS batch_name,
       interp.overall_level, interp.red_count, interp.yellow_count, interp.summary_text,
       :hid AS hospital_id, :hname AS hospital_name
FROM report_info ri
JOIN report_interpretation interp ON interp.report_id = ri.id
JOIN hospital_user hu ON hu.id = ri.user_id
LEFT JOIN batch_import_file bf ON bf.report_task_id = ri.task_id
LEFT JOIN batch_import b ON b.id = bf.batch_id
WHERE interp.status='completed'
  AND (interp.overall_level='red' OR interp.red_count>=3)
  [AND ri.report_date BETWEEN ...]
  [AND ri.gender = :gender]
  [AND <age_group>]
  [AND b.id IN (:batch_ids) 当 batch_ids 非空时]
ORDER BY <sort>
LIMIT :limit OFFSET :offset
```

CSV 导出(`format=csv`):**不分页拉全量**(上限 50000),实现侧先跑 `SELECT COUNT(*)` 评估,超出抛 413,否则用同一 SQL 不带 LIMIT、流式 `StreamingResponse`(csv.writer over generator)输出。

### 6.4 `group_service.py` 主要函数

```python
def get_overview(group_by, filters) -> dict:
    tenant_ids = _resolve_tenants(filters.hospital_ids)   # get_all_hospital_ids 过滤
    rows = _run_parallel(
        worker=lambda hid, hname: _per_tenant_overview(hid, hname, group_by, filters),
        items=[(hid, hname) for hid, hname in tenant_ids],
        max_workers=8,
    )
    return _merge_overview(group_by, filters, rows)

def get_high_risk(filters, sort, page, page_size, fmt) -> ...:
    tenant_ids = _resolve_tenants(filters.hospital_ids)
    if fmt == "csv":
        # 先估总行数
        total = sum(_per_tenant_high_risk_count(hid, filters) for hid, _ in tenant_ids)
        if total > 50_000: raise HTTPException(413, ...)
        return _stream_csv(tenant_ids, filters, sort)    # StreamingResponse
    # json:并发按页(每库页内 offset/limit),merge 后总页内排序
    return _get_high_risk_page(...)
```

**单库失败**:`_per_tenant_overview` 整个 `try/except Exception` 包住,失败时 logger.exception 写 `app.log`,返回 `{"error":"db_unavailable","key":hid,"label":hname}` 给 merge。

### 6.5 logger

`logging.getLogger("app.statistics")`(全局收口 `app.log`,匹配 AGENTS.md logger 表风格)。每次 overview 请求 `logger.info("group_overview group_by=%s took=%.2fs rows=%d errors=%d", ...)`。CSV 导出 `logger.info("group_high_risk_csv rows=%d", n)`。

## 7. 前端实现(admin-portal)

### 7.1 文件结构

```
frontend/packages/admin-portal/
├── package.json                     新增 echo: echarts,echarts-for-react
├── src/
│   ├── router.tsx                   新增 /group-analysis 路由(AuthGuard 已存在)
│   ├── api/
│   │   └── groupAnalysis.ts         调 /statistics/group/overview 与 /statistics/group/high-risk 两个接口
│   │                                  (复用 useAdminStore.getState().api 即 shared apiClient,
│   │                                   它已注入 Authorization: Bearer <JWT>;不带 X-Admin-Token)
│   └── pages/
│       └── group-analysis/
│           ├── GroupAnalysisPage.tsx   主页:FilterBar + ECharts + HighRiskTable 切换 tab
│           └── components/
│               ├── FilterBar.tsx       医院(多选下拉,拉 tenant list)/批次(可填)/日期范围/性别/年龄段;group_by Select
│               ├── OverviewCharts.tsx  按 group_by 渲染:
│               │                          hospital → 各院异常率对比柱 + 三色堆叠柱 + Top10 异常指标横向条
│               │                          batch    → 同上但 key=batch_name
│               │                          age_group→ 异常率折线(age_group × abnormal_rate)+ 人数饼
│               │                          gender   → 三色堆叠柱(M vs F)
│               │                          time_month → 异常率趋势折线
│               └── HighRiskTable.tsx     AntD Table;Sorter;分页;导出 CSV(发 /high-risk?format=csv 触发下载)
```

vite.config.ts 需新增 `server.proxy` 把 `/api` 转发到 `http://localhost:8000`(与 doctor-portal 同),否则 dev 环境跨域请求失败。

### 7.2 ECharts 引入

```ts
// echarts 按需 import(ECharts 5 tree-shake)
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, LegendComponent, TitleComponent, CanvasRenderer]);
import ReactECharts from "echarts-for-react";
```

仅注册用到的组件,生产包预期 < 200KB(gzipped)。

### 7.3 鉴权 / client 适配

无需新建独立 axios 实例。`adminStore.api`(`stores/adminStore.ts:11-13`)即 `createApiClient(getToken)` 返回的 shared `apiClient`,其请求拦截器(`shared/src/api/client.ts:6-15`)把 `adminStore.token`(JWT 字符串)放进 `Authorization: Bearer ...` —— 与本设计后端 `get_current_user` 解码 JWT 一致。

```ts
// admin-portal/src/api/groupAnalysis.ts
import { useAdminStore } from "@/stores/adminStore";

const api = () => useAdminStore.getState().api;

export interface OverviewRow { key: string; label: string; total_people: number;
  red_count: number; yellow_count: number; green_count: number;
  abnormal_rate: number;
  by_gender?: { key: string; count: number }[];
  by_age_group?: { key: string; count: number }[];
  top_abnormal_items?: { item: string; red_count: number }[];
  error?: string;
}
export interface OverviewResponse { group_by: string; filters: any; rows: OverviewRow[]; totals: any; }

export async function getOverview(params: Record<string, any>): Promise<OverviewResponse> {
  const r = await api().get("/statistics/group/overview", { params });
  return r.data;
}
export async function getHighRisk(params: Record<string, any>): Promise<any> {
  const r = await api().get("/statistics/group/high-risk", { params });
  return r.data;
}
/** CSV 导出:浏览器跳转 url,带 Authorization 由同源 fetch 触发? 否 —— 用 blob 下载 */
export async function downloadHighRiskCsv(params: Record<string, any>) {
  const r = await api().get("/statistics/group/high-risk",
    { params: { ...params, format: "csv" }, responseType: "blob" });
  const url = URL.createObjectURL(r.data);
  const a = document.createElement("a");
  a.href = url; a.download = "high-risk.csv"; a.click();
  URL.revokeObjectURL(url);
}
```

vite dev server 走 `server.proxy` 代理到 backend:8000(与 doctor-portal 同),见 §7.1 末尾。

### 7.4 体验细节

- `FilterBar` "查询" 按钮主控提交 trigger overview 拉数据;切 `group_by` 自动重查(若 filters 已存在)。
- `HighRiskTable` 行政区域列(医院)在排序下默认按 red_count desc;CSV 下载按钮在表格右上。
- `db_unavailable` 的 row 在图表里以橙色条 + tooltip "数据库不可用" 显式提示;不影响其它 row。

## 8. 错误处理与边界

- 无活跃 tenant:200 + `rows:[]` + `totals` 全 0;不报错。
- 某 tenant 连不上 / SQL 超时 5s:catch 后该 tenant 进 `rows` 标 `error:"db_unavailable"`,HTTP 200 不影响整体。
- filter 参数非法:`group_by` 非枚举 / `page<1` / `page_size>200` / ISO 日期错 → Pydantic 422;`date_from>date_to` → 400;`hospital_ids` 含未知 id → 忽略未知,不报 404。
- CSV 上限 50000 行 → 413(`code:"PAYLOAD_TOO_LARGE"`)。
- 单库 SQL 超时 5s:catch,同 db_unavailable 处理。
- 鉴权失败:JWT 缺失/过期 → 401;JWT 有效但 `role != "admin"` → 403(`require_role("admin")`);不暴露内部状态。
- 总体异常:global handler 兜底返回 `INTERNAL_ERROR`(已存在 `main.py:52-61`)。
- 日志:`app.statistics` logger 写 `app.log`;异常 `logger.exception` 单库失败 + 概览请求成功 INFO 各一条。

## 9. 测试

`backend/tests/modules/statistics/test_group.py`(新增):

- 参数解析:`group_by` 非枚举→422;`date_from>date_to`→400;多 csv 解析正常解析。
- 每库 SQL:fixture 至少 2 个 tenant(per-hospital in-memory sqlite proxy 或 pytest-mysql);断言聚合行数与三色计数;Top10 异常指标排序正确。
- 跨院 merge:多 tenant fixture 下 `rows` 数量正确;`totals` 累加;`abnormal_rate` 重新加权计算。
- 单库失败:mock 一个 tenant 抛 DBAPIError → `rows` 含 `error:"db_unavailable"`,其它 tenant 不受影响,HTTP 200。
- 高风险 list:fixture 含 `overall_level='red'` 与 `red_count>=3`(但 overall_level='yellow')两条都入结果;`overall_level='green'` 且 `red_count<3` 不入。
- 高风险分页:`page_size=20` 总数 233 → 第 1 页 20 条,第 12 页 13 条。
- CSV:`format=csv` Content-Type/Content-Disposition 含 BOM;超 50000 行 → 413。
- 鉴权:JWT 缺失/无效 → 401;JWT 有效但 `role != "admin"` → 403(`require_role("admin")`);正确 admin JWT → 200。

router 层不另单测,由上述包含 FastAPI dependency 注入 fixture 覆盖;手工 curl 仍提供于 spec(§11)。

前端:复用 admin-portal 现有测试体系(若有 vitest 则加 GroupAnalysisPage 冒烟测试;无则跳过),以手动验收为主。

## 10. 本期不做

- `statistic_cache` 缓存接入 / 异步任务 / 自定义指标分类 / 通用 pivot cube(YAGNI)。
- `report_info.unit_name` 解析写入恢复 — 跨院维度由 `hospital_id` 替代,本期不需要。
- Excel 真二进制导出(openpyxl) — CSV 已覆盖需求。
- 告警阈值规则配置 UI — 判定阈值 `red_count>=3` 写死在代码;后续可加。
- doctor-portal 复用本接口 — 用户角色不符,不做。
- 抽 `infra/mysql/tenant_schema.sql` 共用 DDL — 与本设计无关,留作未来重构。

## 11. 部署 / 验收

- 后端:
  1. 拉新代码;无新依赖(`require_role`、`get_current_user`、`get_all_hospital_ids`、`ThreadPoolExecutor` 全是 stdlib/已用模块)。
  2. 重启 FastAPI(`supervisorctl restart backend` 或 `bash start.sh --no-models`);不需要重启 vLLM / OCR / reranker。
  3. **不动 DDL**,新 tenant 走 `start.sh` 原 DDL 块即可,本设计无新表新列。
  4. 健康验证(需先通过 `/auth/login` 拿 role=admin 的 JWT):
     ```bash
     JWT=$(curl -sX POST http://localhost:8000/api/v1/auth/login \
       -H 'Content-Type: application/json' \
       -d '{"username":"admin","password":"..."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
     curl -s -H "Authorization: Bearer $JWT" \
       'http://localhost:8000/api/v1/statistics/group/overview?group_by=hospital' | head
     # 期望:{"group_by":"hospital","rows":[...],"totals":{...}}
     curl -s -H "Authorization: Bearer $JWT" \
       'http://localhost:8000/api/v1/statistics/group/high-risk?page=1&page_size=5' | head
     ```
- 前端:
  1. `pnpm -F admin-portal add echarts echarts-for-react`
  2. `pnpm -F admin-portal dev`(端口 3003)访问 `/group-analysis`
  3. 验证:筛 2+ 医院 → hospital 对比图显示多柱;切 age_group → 折线;高风险表分页/CSV 正常。

## 12. 兼容性影响

- 不改任何现有表 schema;`hospital_H001` 业务不需要迁移。
- `start.sh` 启动逻辑不变。
- `statistics/router.py` 现有 5 个 endpoint 不动,doctor-portal 现有页面不受影响。
- tenant router 不动,X-Admin-Token 模式保留;本设计的 group router 用 JWT + `require_role("admin")`,两套鉴权并存。

## 13. 风险

| 风险 | 缓解 |
|---|---|
| tenant 数 N 大时 ThreadPool=8 排队延迟 | 当前 tenant 数 < 10,8 workers 充分覆盖;真上量再评估 |
| 跨库聚合 SQL 在大量 report_info 上扫描慢 | 单库 SQL 5s 超时 + logger 警告;真出现慢查询再启用 `statistic_cache` |
| `batch_import_file.report_task_id` 与 `report_info.task_id` 间真实链路在 models 与 DDL 间有偏差 | 实现期按 `report/models.py:24-37` 与 `report/batch_models.py:5-37` 列名落实;若 JOIN 出现漏行,退化为 `report_task` 中转表两跳 |
| ECharts 包大小影响 admin-portal 首屏 | 按需 import(§7.2);预期 < 200KB gzipped |
| 平台 admin 用户(`role='admin'` 且 `hospital_id=NULL`)若不在 platform_user 维护,会导致 admin-portal 无法登录使用本功能 | 部署时由运维通过现有 register 接口或直接 SQL 插入 `platform_user` 行;不进入本设计交付范围 |
| CSV 超大行数导致内存压力 | 50000 行硬上限 + StreamingResponse 写流 |
| 现有 statistics router 没 admin 鉴权(沿用 ContextVar 单院上下文),与本设计 admin JWT 鉴权风格不一致 | 不强行收紧现有 router(向后兼容);新 group router 独立鉴权 |
| 单库失败标 `error` 让 row 总计指标偏差(失败库不计入 totals) | 用户接受:失败的库无法提供指标,merge 时该库数据自然缺失,前端在 `db_unavailable` row 上提示 |