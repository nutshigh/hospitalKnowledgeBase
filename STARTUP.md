# 项目启动指南

## 前置依赖

- Docker Desktop（已安装）
- Python 3.12+ / uv（已安装）
- Node.js + npm（已安装）

---

## 1. 启动基础设施（Docker）

```bash
cd backend/docker
docker-compose up -d
```

启动 4 个服务：
- MySQL (3306) — `root / root123`
- RabbitMQ (5672 + 管理界面 15672) — `guest / guest`
- Etcd (2379)
- Milvus (19530)

验证：
```bash
docker ps
# 应该看到 4 个容器 running
```

---

## 2. 初始化数据库

MySQL 容器启动时会自动执行 `docker/mysql/init/` 下的脚本创建 `hospital_template` 库。手动创建第一家医院：

```bash
docker exec -i $(docker ps -qf "name=mysql") mysql -uroot -proot123 <<SQL
CALL hospital_template.create_hospital_database('H001');
SQL
```

---

## 3. 配置环境变量

```bash
cd backend
cp .env.example .env
```

---

## 4. 启动后端

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

验证：`http://localhost:8000/api/v1/health` 返回 `{"status":"ok"}`

---

## 5. 启动前端（选一或多端）

```bash
cd frontend

# 用户端 (端口 3001)
npm run dev -w @hospital/user-portal

# 医生端 (端口 3002)
npm run dev -w @hospital/doctor-portal

# 管理后台 (端口 3003)
npm run dev -w @hospital/admin-portal
```

---

## 6. 创建测试用户

```bash
# 注册医生账号
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"doctor1","password":"123456","role":"doctor","hospital_id":"H001"}'

# 注册普通用户
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"user1","password":"123456","role":"user","hospital_id":"H001"}'
```

---

## 启动命令汇总（一次性）

```bash
# 1. 基础设施
cd backend/docker && docker-compose up -d

# 2. 初始化医院库
docker exec -i $(docker ps -qf "name=mysql") mysql -uroot -proot123 -e "CALL hospital_template.create_hospital_database('H001');"

# 3. 后端
cd backend && cp -n .env.example .env && uv run uvicorn app.main:app --reload --port 8000

# 4. 前端（新终端窗口）
cd frontend && npm run dev -w @hospital/doctor-portal
```
