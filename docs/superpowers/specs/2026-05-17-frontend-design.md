# 前端设计文档 — 三端前端架构与组件设计

## 在整体架构中的位置

```
接入层
├── 用户端（移动优先/Web）   ← 本文档 Part A
├── 医生端（Web）           ← 本文档 Part B
└── 管理后台（Web）         ← 本文档 Part C
        ↓
     API 网关层
```

三套前端各自独立部署，共用 UI 组件库和 API 请求层，但路由、状态、页面完全独立。

---

## 技术栈

| 类别 | 选型 | 用途 |
|------|------|------|
| 前端框架 | React 18 | 三端统一框架 |
| UI 组件库 | Ant Design 5 | 表格、表单、布局、通知等 |
| 图表库 | ECharts + echarts-for-react | 统计图表、趋势图、BI 看板 |
| 路由 | React Router 6 | 三端各自独立路由 |
| 状态管理 | Zustand（轻量） | 全局状态（用户信息、医院上下文） |
| HTTP 客户端 | Axios | API 请求封装、拦截器、Token 注入 |
| 移动端适配 | antd-mobile（用户端） | 移动端上传、列表等 |
| 构建工具 | Vite | 三套前端独立构建 |

---

## Part A：用户端（体检者端）

### A1. 页面路由结构

```
/                         → 首页（我的报告列表）
/login                    → 登录/注册
/upload                   → 上传报告
/report/:id               → 报告详情
/report/:id/indicator/:iid → 指标详情页
/profile                  → 个人中心
```

### A2. 页面组件树

**首页 — 我的报告列表**

```
HomePage
├── Header（医院名称、用户头像、退出）
├── StatusTabs（全部 / 处理中 / 已完成）
├── ReportCardList
│   └── ReportCard × N
│       ├── ReportDate（报告日期）
│       ├── StatusTag（排队中/解析中/解读中/已完成/失败）
│       ├── OverallLevelBadge（红/黄/绿 圆点）
│       └── Thumbnail（缩略图）
└── UploadFAB（悬浮按钮 → 跳转上传页）
```

**上传报告页**

```
UploadPage
├── NavBar（返回）
├── UploadModeSwitch（拍照 / 相册 / 文件）
├── ImagePicker / FilePicker
├── PreviewArea（预览）
│   └── QualityFeedback（模糊/倾斜检测提示）
└── SubmitButton（提交上传）
    └── ProgressModal（上传进度 + 解析状态）
```

**报告详情页**

```
ReportDetailPage
├── NavBar（返回）
├── PersonalInfoCard（姓名、性别、年龄、检查日期）
├── OverallAssessment
│   ├── LevelGauge（红黄绿仪表盘）
│   └── SummaryText（综合小结）
├── IndicatorTable
│   ├── IndicatorRow × N
│   │   ├── ItemName（指标名称）
│   │   ├── ResultValue + Unit
│   │   ├── RefRange（参考区间）
│   │   ├── ColorBadge（红/黄/绿）
│   │   └── ExpandArrow → 展开解读
│   └── IndicatorDetail（展开后）
│       ├── TrendChart（历年趋势折线图）
│       ├── Explanation（AI 解读文字）
│       └── Suggestion（健康建议）
└── ActionBar（分享报告 / 下载 PDF）
```

**个人中心页**

```
ProfilePage
├── NavBar
├── UserInfoCard（头像、姓名、手机号）
├── MenuList
│   ├── 消息通知（未读红点）
│   └── 设置
└── LogoutButton
```

### A3. 状态管理（Zustand Store）

```
userStore:
  - token, userInfo, hospitalId

reportStore:
  - reportList[], loading
  - currentReport, indicators[]
  - uploadTaskId, uploadStatus

notificationStore:
  - unreadCount, notifications[]
```

### A4. API 请求拦截器

```
请求拦截:
  - 自动注入 Authorization: Bearer {token}
  - 自动注入 X-Hospital-Id（从 userStore 获取）

响应拦截:
  - 401 → 清除 token → 跳转登录
  - 网络错误 → Toast 提示
```

---

## Part B：医生端（体检科/医生）

### B1. 页面路由结构

```
/                             → 工作台首页
/login                        → 医生登录

# 报告管理
/reports                      → 报告列表
/reports/:id                  → 报告详情（含审核）

# 高风险人群
/high-risk                    → 高风险人群看板
/high-risk/:userId            → 个人风险详情

# 知识库管理
/knowledge                    → 知识条目列表
/knowledge/import             → 文档导入
/knowledge/:id                → 知识详情/编辑

# 三色规则配置
/triage-rules                 → 规则列表
/triage-rules/:id             → 规则编辑

# 统计分析
/statistics/health-profile    → 健康画像
/statistics/cross-compare     → 多维交叉对比
/statistics/trend             → 趋势分析
/statistics/export            → 报表导出

# 系统管理
/settings/hospital            → 医院信息配置
/settings/users               → 用户管理
/settings/dispatch            → 并发控制 & 资源监控
```

### B2. 核心页面组件树

**工作台首页**

```
DashboardPage
├── Sidebar（导航菜单 + 医院名称）
├── TopBar（用户头像 + 通知铃铛）
└── Content
    ├── StatCards（今日统计卡片行）
    │   ├── TotalReportsCard（报告总数）
    │   ├── RedZoneCountCard（红区人数）
    │   ├── PendingReviewCard（待审核数）
    │   └── ParsingQueueCard（解析排队数）
    ├── RecentAlerts（最近红区预警列表）
    └── WeeklyTrend（本周异常率趋势小图）
```

**报告管理列表**

```
ReportListPage
├── FilterBar
│   ├── DateRangePicker
│   ├── UnitSelect（单位筛选）
│   ├── StatusSelect（状态筛选）
│   ├── ColorLevelSelect（红/黄/绿筛选）
│   └── SearchInput（姓名/报告号搜索）
├── ReportTable（Ant Table）
│   ├── 姓名、性别、年龄
│   ├── 单位/部门
│   ├── 报告日期
│   ├── ColorLevelTag
│   ├── StatusTag
│   └── ActionColumn（查看详情）
└── Pagination
```

**报告详情（含审核）**

```
ReportDetailPage
├── BackButton
├── ReportInfoHeader
├── IndicatorTable（同用户端 + 审核操作列）
│   └── AuditAction
│       ├── ApproveButton（确认 AI 解读）
│       ├── EditButton（修改解读/等级）
│       └── CommentInput（审核备注）
├── AIInterpretationPanel（AI 生成的解读文本）
├── DoctorReviewPanel（医生审核区）
│   ├── ReviewStatus（已审核/待审核）
│   ├── DoctorComment
│   └── SubmitReviewButton
└── HistoryCompare（历年对比面板）
    └── TrendChart（多指标趋势图）
```

**高风险人群看板**

```
HighRiskPage
├── SummaryBar
│   ├── RedTotalCount
│   ├── ByUnitChart（按单位分布柱状图）
│   └── ByIndicatorChart（按异常指标分布）
├── RedZoneTable
│   ├── 姓名、单位、红区指标数、主要异常项
│   ├── ActionColumn
│   │   ├── ViewDetailButton
│   │   ├── RecheckNotifyButton（一键复查通知）
│   │   └── InterventionReportButton（生成干预报告）
└── BatchActions
    ├── SelectAllCheckbox
    ├── BatchNotifyButton
    └── ExportRedListButton
```

**知识库管理列表**

```
KnowledgePage
├── LeftPanel（分类树）
│   └── CategoryTree（可拖拽排序）
├── RightPanel
│   ├── ToolBar
│   │   ├── SearchInput
│   │   ├── AddEntryButton
│   │   └── ImportDocButton
│   ├── EntryTable
│   │   ├── 标题、分类、来源、更新时间
│   │   └── ActionColumn（编辑/删除/查看）
│   └── Pagination
└── ImportModal（文档导入弹窗）
    ├── FileUpload（拖拽上传）
    ├── CategorySelect
    └── ProgressBar（导入进度）
```

**三色规则配置**

```
TriageRulePage
├── RuleTable
│   ├── 规则名称、类型、适用指标、等级、状态
│   ├── Switch（启用/禁用）
│   └── ActionColumn（编辑/删除）
├── AddRuleButton → RuleEditModal
└── RuleEditModal
    ├── RuleNameInput
    ├── RuleTypeSelect（数值范围/关键指标/组合/趋势）
    ├── ConditionBuilder（动态表单，根据规则类型变化）
    │   ├── SimpleCondition（阈值设置）
    │   └── ComboCondition（多指标组合设置）
    └── ColorLevelSelect
```

**BI 分析看板 — 健康画像**

```
HealthProfilePage
├── FilterBar
│   ├── UnitSelect（单位/部门）
│   └── YearSelect（年度）
├── DiseaseSpectrumChart（疾病谱饼图/柱状图）
├── TopHighRiskChart（高发疾病排行）
└── ByGenderChart（男女异常率对比）
```

**BI 分析看板 — 多维对比**

```
CrossComparePage
├── DimensionSelector（X 轴维度：单位/性别/年龄）
├── MetricSelector（Y 轴指标：某病异常率/均值）
├── CompareChart（交叉对比柱状图/热力图）
└── DataTable（对比明细表，可导出）
```

**BI 分析看板 — 趋势分析**

```
TrendPage
├── IndicatorSelector（选择疾病/指标）
├── YearRangeSlider（年份范围）
├── TrendChart（折线图，年度异常率变化）
└── TrendTable（年度明细数据表）
```

**并发控制 & 资源监控**

```
DispatchPage
├── ResourceMonitorPanel
│   ├── CPUGauge（环形进度）
│   ├── MemoryGauge
│   ├── GPUGauge
│   └── GPUMemoryBar
├── QueueMonitorPanel
│   ├── ParsingQueueDepth
│   ├── InterpretationQueueDepth
│   └── QueueTrendChart（近 30 分钟折线图）
├── WorkerControlPanel
│   ├── ParsingWorkerSlider（滑块调并发数）
│   └── InterpretationWorkerSlider
└── TaskStatPanel
    ├── TodayCompleteCount
    ├── TodayFailCount
    └── AvgDuration
```

### B3. 医生端布局框架

```
┌──────────────────────────────────────────┐
│  Sidebar (固定左侧)  │  Header (固定顶部)   │
│  ┌────────────────┐  │  ┌──────────────┐  │
│  │ Logo           │  │  │ 面包屑 + 通知  │  │
│  │ 工作台          │  │  └──────────────┘  │
│  │ 报告管理        │  ├────────────────────┤
│  │ 高风险人群      │  │                    │
│  │ 知识库管理      │  │   Content          │
│  │ 三色规则配置    │  │   （页面内容区）    │
│  │ ————————       │  │                    │
│  │ 统计分析        │  │                    │
│  │  ├ 健康画像     │  │                    │
│  │  ├ 多维对比     │  │                    │
│  │  ├ 趋势分析     │  │                    │
│  │  └ 报表导出     │  │                    │
│  │ ————————       │  │                    │
│  │ 系统管理        │  │                    │
│  │  ├ 医院配置     │  │                    │
│  │  ├ 用户管理     │  │                    │
│  │  └ 调度管理     │  │                    │
│  └────────────────┘  │                    │
└──────────────────────────────────────────┘
```

### B4. 状态管理（Zustand Store）

```
authStore:
  - token, doctorInfo, hospitalId, hospitalName

reportStore:
  - filterParams, reportList[], total, currentReport

highRiskStore:
  - redZoneList[], filterParams

knowledgeStore:
  - categories[], selectedCategoryId
  - entryList[], currentEntry

triageRuleStore:
  - rules[], editingRule

statisticsStore:
  - globalDateRange
  - healthProfile, crossCompareData, trendData

dispatchStore:
  - resourceMetrics（实时轮询）, workerConfig, queueStatus
```

---

## Part C：管理后台（平台超管）

### C1. 页面路由结构

```
/                            → 平台概览
/login                       → 超管登录
/hospitals                   → 医院租户列表
/hospitals/:id               → 医院详情/配置
/monitor                     → 系统监控（全局资源）
/config                      → 运营配置（模板管理、通知配置）
```

### C2. 核心页面

**平台概览**

```
PlatformDashboard
├── CrossHospitalStatCards（跨院汇总统计卡片）
├── HospitalCompareChart（各医院数据对比图）
└── SystemHealthPanel（全局系统健康状态）
```

**医院租户管理**

```
HospitalListPage
├── HospitalTable
│   ├── 医院名称、接入时间、状态、报告总数
│   └── ActionColumn（编辑/禁用/查看数据）
└── AddHospitalButton → HospitalFormModal
    ├── HospitalName
    ├── DatabaseConfig（自动创建隔离库）
    └── AdminAccount（初始医生账号）
```

---

## Part D：共享组件库

三端抽取公共组件，统一维护：

| 组件 | 说明 | 使用端 |
|------|------|--------|
| ColorBadge | 红/黄/绿标签 | 用户端、医生端 |
| StatusTag | 任务状态标签 | 用户端、医生端 |
| IndicatorTable | 指标表格（含色标） | 用户端、医生端 |
| TrendChart | 历年趋势折线图 | 用户端、医生端 |
| ReportCard | 报告缩略卡片 | 用户端 |
| FileUploader | 文件上传组件（拖拽+预览） | 用户端、医生端 |
| FilterBar | 筛选栏容器 | 医生端 |
| StatCard | 统计数值卡片 | 医生端、管理后台 |
| AuthGuard | 路由鉴权守卫 | 三端通用 |

---

## 前端项目目录结构

```
frontend/
├── packages/
│   ├── shared/                  # 共享组件库
│   │   ├── components/          # 公共组件
│   │   │   ├── ColorBadge/
│   │   │   ├── StatusTag/
│   │   │   ├── IndicatorTable/
│   │   │   ├── TrendChart/
│   │   │   └── AuthGuard/
│   │   ├── hooks/               # 公共 Hooks
│   │   ├── utils/               # 工具函数
│   │   └── api/                 # API 请求封装 + 类型定义
│   ├── user-portal/             # 用户端
│   │   ├── pages/
│   │   ├── components/
│   │   ├── stores/
│   │   └── router.tsx
│   ├── doctor-portal/           # 医生端
│   │   ├── pages/
│   │   │   ├── Dashboard/
│   │   │   ├── Reports/
│   │   │   ├── HighRisk/
│   │   │   ├── Knowledge/
│   │   │   ├── TriageRules/
│   │   │   ├── Statistics/
│   │   │   └── Settings/
│   │   ├── components/
│   │   ├── stores/
│   │   └── router.tsx
│   └── admin-portal/            # 管理后台
│       ├── pages/
│       ├── stores/
│       └── router.tsx
└── vite.config.ts               # 各端独立构建配置
```

---

## 前端错误处理

| 场景 | 处理方式 |
|------|----------|
| 网络请求失败 | Axios 响应拦截统一 Toast 提示；关键操作（上传）额外提示重试 |
| Token 过期 | 401 自动跳转登录页，清除本地 Token |
| 大文件上传失败 | 前端分片上传（>10MB），断点续传，显示进度 |
| 图表渲染失败 | Error Boundary 捕获，显示"图表加载失败"占位 |
| 表单校验失败 | Ant Design Form 内置校验，字段下方红色提示 |
| 长时间无数据 | 空状态占位（Empty） + 引导操作提示 |
