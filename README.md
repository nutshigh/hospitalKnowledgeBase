# 医院AI体检报告识别健康管理系统

面向多家医院的 AI 体检报告识别与管理平台。系统分为**用户端**（体检者上传报告、查看 AI 解读）和**医生端**（报告审核、知识库管理、统计分析）及**管理后台**（医院租户管理）。

---

## 技术栈

| 类别 | 选型 |
|------|------|
| 后端语言 | Python 3.12+ |
| Web 框架 | FastAPI |
| 关系型数据库 | MySQL 8（按医院数据库级隔离） |
| 向量数据库 | Milvus（按医院命名空间隔离） |
| 消息队列 | RabbitMQ |
| 大语言模型 | 本地部署 vLLM / 远端 OpenAI 兼容 API（可切换） |
| 前端框架 | React 18 + TypeScript |
| UI 组件库 | Ant Design 5 |
| 构建工具 | Vite |
| API 网关 | APISIX |
| 包管理 | uv (Python) / npm (Node) |

---

## 快速启动

### 环境要求

- Docker Desktop
- Python 3.12+ / [uv](https://docs.astral.sh/uv/)
- Node.js 18+ / npm

### 1. 启动基础设施

```bash
cd backend/docker
docker-compose up -d
```

启动 MySQL (3306)、RabbitMQ (5672)、Milvus (19530)。

### 2. 初始化数据库

```bash
docker exec -i $(docker ps -qf "name=mysql") mysql -uroot -proot123 \
  -e "CALL hospital_template.create_hospital_database('H001');"
```

### 3. 启动后端

```bash
cd backend
cp -n .env.example .env
uv run uvicorn app.main:app --reload --port 8000
```

### 4. 启动前端

```bash
cd frontend
npm install

# 医生端 (:3002)
npm run dev -w @hospital/doctor-portal

# 用户端 (:3001)
npm run dev -w @hospital/user-portal

# 管理后台 (:3003)
npm run dev -w @hospital/admin-portal
```

### 5. 创建测试用户

```bash
# 医生
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"doctor1","password":"123456","role":"doctor","hospital_id":"H001"}'

# 用户
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"user1","password":"123456","role":"user","hospital_id":"H001"}'
```

---

## 系统架构

```
┌──────────────────────────────────────────────────┐
│                    接入层                          │
│   医生端 Web   │   用户端(移动/Web)   │   管理后台    │
├──────────────────────────────────────────────────┤
│                  API 网关 (APISIX)                 │
├──────────────────────────────────────────────────┤
│                    业务模块层                       │
│  ┌──────────┬──────────┬──────────┬──────────┐    │
│  │ 知识库模块 │ 报告解析模块│ AI解读模块 │ 统计分析模块│    │
│  └──────────┴──────────┴──────────┴──────────┘    │
│  ┌──────────┐                                     │
│  │ 调度管理模块│                                     │
│  └──────────┘                                     │
├──────────────────────────────────────────────────┤
│               基础设施层                            │
│  文件存储  │  RabbitMQ  │  Milvus向量库  │  LLM    │
├──────────────────────────────────────────────────┤
│               数据层                               │
│     MySQL(按医院分库)  │  Milvus(按命名空间隔离)     │
└──────────────────────────────────────────────────┘
```

### 核心数据流

```
用户上传报告 → [报告解析] → OCR/VLM提取 → 结构化数据
                                          ↓
                                    [AI解读模块]
                                    ├── 规则引擎判定（红/黄/绿）
                                    ├── 知识库检索（RAG）
                                    └── LLM生成解读文字
                                          ↓
                                    写入结果库 → 用户查看
                                          ↓
                                    [统计分析] → 报表/看板
```

---

## 模块说明

### 知识库模块
- 医疗知识条目 CRUD + 分类管理
- PDF/Word/Excel/文本批量导入
- 文档分段 → Embedding → Milvus 向量存储
- 语义检索 API（供 AI 解读模块调用）
- 动态知识更新 + 局部索引重建

### 报告解析模块
- 多端文件上传（移动端拍照/Web 拖拽）
- 图像预处理（模糊检测/寻边裁剪/倾斜校正）
- 多格式解析（PDF/Word/图片）
- VLM 结构化信息提取
- 医学术语标准化
- 异步任务状态机 + RabbitMQ Worker

### AI 解读模块
- 三色规则引擎（数值范围/关键指标/组合/趋势规则）
- 规则可配置（医生端后台管理）
- 历年对比研判
- LLM 生成解读文字 + 健康建议
- 高风险人群汇总 + 预警看板

### 统计分析模块
- 健康画像（疾病谱/高发分布）
- 多维交叉对比（性别/年龄/单位）
- 趋势分析（慢性病/重大疾病变化）
- BI 看板数据 API
- 报表导出（Word/Excel/PDF）

### 调度管理模块
- 弹性算力分配 + 可视化并发控制
- 任务优先级队列
- 失败自动重试 + 死信队列
- 系统资源监控大屏

---

## 前端页面

### 用户端（端口 3001）
| 页面 | 路由 | 说明 |
|------|------|------|
| 登录 | `/login` | 用户登录 |
| 报告列表 | `/` | 我的体检报告 |
| 上传报告 | `/upload` | 拍照/上传文件 |
| 报告详情 | `/report/:id` | 指标解读 + AI 建议 |
| 个人中心 | `/profile` | 设置 + 退出 |

### 医生端（端口 3002）
| 页面 | 路由 | 说明 |
|------|------|------|
| 工作台 | `/` | 当日统计概览 |
| 报告管理 | `/reports` | 报告列表 + 筛选 |
| 报告详情 | `/reports/:id` | 审核解读结果 |
| 高风险人群 | `/high-risk` | 红区名单 + 复查通知 |
| 知识库管理 | `/knowledge` | CRUD + 文档导入 |
| 三色规则配置 | `/triage-rules` | 自定义阈值规则 |
| 健康画像 | `/statistics/health-profile` | 疾病谱分布 |
| 多维对比 | `/statistics/cross-compare` | 交叉比对 |
| 趋势分析 | `/statistics/trend` | 历年变化 |
| 报表导出 | `/statistics/export` | Word/Excel/PDF |
| 调度管理 | `/dispatch` | 资源监控 + 并发控制 |

### 管理后台（端口 3003）
| 页面 | 路由 | 说明 |
|------|------|------|
| 平台概览 | `/` | 医院租户管理 |

---

## 项目结构

```
hospitalKnowledgeBase/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口
│   │   ├── config.py               # 配置管理
│   │   ├── api/                    # 认证 + 健康检查
│   │   ├── core/                   # 基础设施封装
│   │   │   ├── database.py         # MySQL 多库路由
│   │   │   ├── milvus.py           # 向量库客户端
│   │   │   ├── rabbitmq.py         # 消息队列客户端
│   │   │   ├── security.py         # JWT 认证
│   │   │   ├── embedding.py        # Embedding 客户端
│   │   │   ├── doc_parser.py       # 文档解析器
│   │   │   ├── image_preprocess.py # 图像预处理
│   │   │   ├── vlm_client.py       # VLM 客户端
│   │   │   ├── llm_client.py       # LLM 客户端
│   │   │   └── term_normalizer.py  # 术语标准化
│   │   ├── middleware/             # 中间件
│   │   ├── models/                 # SQLAlchemy 基础模型
│   │   └── modules/
│   │       ├── knowledge/          # 知识库模块
│   │       ├── report/             # 报告解析模块
│   │       ├── interpretation/     # AI 解读模块
│   │       ├── statistics/         # 统计分析模块
│   │       └── dispatch/           # 调度管理模块
│   ├── docker/                     # Docker Compose + SQL 初始化
│   └── pyproject.toml
├── frontend/
│   └── packages/
│       ├── shared/                 # 共享组件 + API 客户端
│       ├── user-portal/            # 用户端
│       ├── doctor-portal/          # 医生端
│       └── admin-portal/           # 管理后台
├── apisix/                         # API 网关配置
└── docs/                           # 设计文档 + 实现计划
```

---

## API 概览

| 模块 | 前缀 | 主要端点 |
|------|------|----------|
| 认证 | `/api/v1/auth` | login, register, me |
| 知识库 | `/api/v1/knowledge` | categories, entries, import, reindex |
| 知识库(内部) | `/api/v1/knowledge/internal` | search |
| 报告 | `/api/v1/reports` | upload, tasks, reports |
| 解读 | `/api/v1/interpretations` | 解读查询, high-risk, rules |
| 统计 | `/api/v1/statistics` | dashboard, health-profile, trend, export |
| 调度 | `/api/v1/dispatch` | metrics, queues, config |

---

## 常见问题 (Troubleshooting)

### 1. Docker 镜像拉取失败 (Docker Hub 不可达)

**现象：** `docker pull` 报 `connection timed out` 或 `content size of zero`。

**原因：** Docker Hub 在某些网络环境下不可达（例如中国大陆网络），且部分国内镜像源已停止服务。

**解决：** 使用可用的镜像代理拉取，然后打标签：

```bash
# 测试当前可用的代理
curl -sI --connect-timeout 5 https://dockerproxy.net/v2/ | head -1

# 通过代理拉取镜像
docker pull dockerproxy.net/library/mysql:8.0
docker pull dockerproxy.net/library/rabbitmq:3.12-management

# 打回原始标签
docker tag dockerproxy.net/library/mysql:8.0 mysql:8.0
docker tag dockerproxy.net/library/rabbitmq:3.12-management rabbitmq:3.12-management
```

备选代理地址（按可用性测试顺序）：
- `dockerproxy.net`
- `docker.m.daocloud.io`
- `docker.1ms.run`

> **注意：** 不要将代理地址写入 `/etc/docker/daemon.json` 的 `registry-mirrors` 再重启 Docker Desktop — 重启后可能导致 Milvus/etcd 等已有容器丢失配置。

### 2. MySQL 端口 3306 冲突

**现象：** 后端启动后注册/登录接口报 `Access denied for user 'root'@'localhost'`，但 `docker exec` 进容器用相同密码可以连接。

**排查步骤：**
```bash
# 检查 3306 端口实际响应的 MySQL 版本
echo "SELECT VERSION();" | timeout 3 nc localhost 3306

# 对比 Docker 容器内的版本
docker exec docker-mysql-1 mysql -uroot -proot123 -e "SELECT VERSION();"
```

如果两个版本号不一致（如前者 8.0.33，后者 8.0.46），说明宿主机上有另一个 MySQL 实例占用了 3306 端口，Docker Desktop 的端口转发优先路由到了那个实例。

**解决：** 将 Docker MySQL 映射到其他端口（如 3307），同步修改 `.env` 中的 `MYSQL_PORT`。

```bash
# 停止并重建容器到新端口
docker stop docker-mysql-1 && docker rm docker-mysql-1
docker run -d --name docker-mysql-1 \
  --network docker_default \
  -e MYSQL_ROOT_PASSWORD=root123 \
  -e MYSQL_CHARACTER_SET_SERVER=utf8mb4 \
  -e MYSQL_COLLATION_SERVER=utf8mb4_unicode_ci \
  -p 3307:3306 \
  -v docker_mysql_data:/var/lib/mysql \
  mysql:8.0
```

### 3. MySQL 认证插件兼容性

**现象：** PyMySQL 连接 MySQL 8.0 报 `Access denied`，即使密码正确。

**原因：** MySQL 8.0 默认使用 `caching_sha2_password` 插件，PyMySQL 在某些场景下不兼容。

**解决：** 创建专用用户并指定 `mysql_native_password` 插件：

```sql
CREATE USER IF NOT EXISTS 'app'@'%' IDENTIFIED WITH mysql_native_password BY 'root123';
GRANT ALL PRIVILEGES ON *.* TO 'app'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
```

### 4. passlib 与 bcrypt 5.x 不兼容

**现象：** 注册接口报 `ValueError: password cannot be longer than 72 bytes`。

**原因：** `passlib` 已停止维护，不兼容 `bcrypt >= 4.1`。本项目已改用原生 `bcrypt` 库（见 `backend/app/core/security.py`）。

如果遇到类似问题，替换方案：
```python
# 废弃写法 (passlib)
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
hash = pwd_context.hash(password)

# 推荐写法 (原生 bcrypt)
import bcrypt
hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
check = bcrypt.checkpw(password.encode(), hash.encode())
```

### 5. MySQL 存储过程不支持 PREPARE 协议

**现象：** 通过 `mysql -e` 调用存储过程 `CALL hospital_template.create_hospital_database('H001')` 报 `ERROR 1295: This command is not supported in the prepared statement protocol yet`。

**原因：** MySQL CLI 默认使用 prepared statement 协议，但存储过程内部的 `PREPARE` 不支持嵌套。

**解决：** 使用 heredoc 方式传 SQL，或手动执行建库建表语句（参考 `backend/docker/mysql/init/` 下的 SQL 脚本）。
