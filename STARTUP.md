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

---

## 常见问题

### Docker 镜像拉取失败
如果 `docker pull` 超时或报错（Docker Hub 不可达），通过代理拉取：
```bash
docker pull dockerproxy.net/library/mysql:8.0
docker pull dockerproxy.net/library/rabbitmq:3.12-management
docker tag dockerproxy.net/library/mysql:8.0 mysql:8.0
docker tag dockerproxy.net/library/rabbitmq:3.12-management rabbitmq:3.12-management
```
备选代理：`docker.m.daocloud.io`, `docker.1ms.run`。

### 端口 3306 冲突
如果连接 MySQL 报 `Access denied` 但 docker exec 可以连接，检查 3306 是否被宿主机 MySQL 占用：
```bash
echo "SELECT VERSION();" | timeout 3 nc localhost 3306   # 看版本号
docker exec docker-mysql-1 mysql -uroot -proot123 -e "SELECT VERSION();"
```
版本不一致说明端口被占用，改用 3307 端口启动容器，并修改 `.env` 中 `MYSQL_PORT=3307`。

### MySQL 认证插件
PyMySQL 连 MySQL 8.0 可能因 `caching_sha2_password` 认证失败。创建 `mysql_native_password` 用户：
```sql
CREATE USER 'app'@'%' IDENTIFIED WITH mysql_native_password BY 'root123';
GRANT ALL PRIVILEGES ON *.* TO 'app'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
```

### passlib / bcrypt 不兼容
`passlib` 与 `bcrypt >= 4.1` 不兼容，已改用原生 `bcrypt` 库（`backend/app/core/security.py`）。
