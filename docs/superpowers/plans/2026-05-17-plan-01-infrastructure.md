# 基础设施搭建 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建项目骨架、数据库、消息队列、向量存储、认证服务和 API 网关，为后续 5 个业务模块提供可运行的基础设施。

**Architecture:** 后端采用 FastAPI 项目结构，按模块划分子目录；前端三套独立 Vite + React 项目；MySQL 数据库级隔离（模板库 + 医院库模式）；RabbitMQ 定义交换机与队列；Milvus 按医院命名空间隔离；APISIX 统一网关入口。

**Tech Stack:** Python 3.10+, FastAPI, MySQL 8, Milvus, RabbitMQ, APISIX, JWT, Docker Compose

---

## 文件结构

```
hospitalKnowledgeBase/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI 应用入口
│   │   ├── config.py                  # 配置管理（环境变量）
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── database.py            # MySQL 连接池 + 多库路由
│   │   │   ├── milvus.py              # Milvus 客户端封装
│   │   │   ├── rabbitmq.py            # RabbitMQ 连接 + 发布/消费封装
│   │   │   ├── security.py            # JWT 生成/验证 + 密码哈希
│   │   │   └── dependencies.py        # FastAPI Depends（get_db, get_current_user 等）
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   └── base.py                # SQLAlchemy Base + Mixin（id, created_at, updated_at）
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py                # 登录/注册/Token 刷新
│   │   │   └── health.py              # 健康检查端点
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   └── hospital_context.py    # 从 Token 提取 hospital_id 注入上下文
│   │   └── utils/
│   │       ├── __init__.py
│   │       └── exceptions.py          # 全局异常处理
│   ├── requirements.txt
│   ├── Dockerfile
│   └── alembic/                       # 数据库迁移
│       └── env.py
├── frontend/
│   ├── packages/
│   │   ├── shared/                    # 共享组件库（骨架）
│   │   ├── user-portal/              # 用户端（骨架）
│   │   ├── doctor-portal/            # 医生端（骨架）
│   │   └── admin-portal/             # 管理后台（骨架）
│   └── package.json
├── docker/
│   ├── docker-compose.yml             # 本地开发环境编排
│   ├── mysql/
│   │   └── init/
│   │       ├── 01_template_db.sql     # 模板库建表脚本
│   │       └── 02_hospital_created.sql # 医院库创建存储过程
│   ├── rabbitmq/
│   │   └── definitions.json           # 交换机/队列定义
│   └── milvus/
│       └── init.sh                    # 初始化脚本
├── apisix/
│   └── config.yml                     # APISIX 路由+上游配置
└── .env.example                       # 环境变量示例
```

---

### Task 1: 后端项目骨架

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/config.py`
- Create: `backend/app/utils/__init__.py`
- Create: `backend/app/utils/exceptions.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/health.py`

- [ ] **Step 1: 编写 requirements.txt**

```txt
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
pymysql==1.1.1
alembic==1.14.0
pymilvus==2.5.0
pika==1.3.2
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.18
pydantic-settings==2.7.0
httpx==0.28.1
```

- [ ] **Step 2: 编写 config.py**

```python
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Hospital AI System"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # MySQL
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_TEMPLATE_DB: str = "hospital_template"  # 模板库，存放平台级表

    # Milvus
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530

    # RabbitMQ
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"

    # JWT
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # File Storage
    FILE_STORAGE_ROOT: str = "./storage"

    class Config:
        env_file = ".env"


settings = Settings()
```

- [ ] **Step 3: 编写异常处理**

`backend/app/utils/exceptions.py`:
```python
from fastapi import HTTPException, status


class AppException(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str = ""):
        self.code = code
        super().__init__(status_code=status_code, detail=detail)


class NotFoundException(AppException):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail, code="NOT_FOUND")


class UnauthorizedException(AppException):
    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, code="UNAUTHORIZED")


class ForbiddenException(AppException):
    def __init__(self, detail: str = "Forbidden"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail, code="FORBIDDEN")


class ValidationException(AppException):
    def __init__(self, detail: str = "Validation error"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail, code="VALIDATION_ERROR")
```

- [ ] **Step 4: 编写 main.py**

`backend/app/main.py`:
```python
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.api.health import router as health_router
from app.api.auth import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api/v1", tags=["health"])
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "INTERNAL_ERROR"},
        )

    return app


app = create_app()
```

- [ ] **Step 5: 编写健康检查 API**

`backend/app/api/health.py`:
```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok"}
```

- [ ] **Step 6: 验证运行**

Run: `cd backend && uvicorn app.main:app --reload --port 8000`

Expected: `curl http://localhost:8000/api/v1/health` 返回 `{"status":"ok"}`

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "feat: add backend project skeleton with FastAPI"
```

---

### Task 2: MySQL 连接池与多库路由

**Files:**
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/database.py`
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/base.py`
- Create: `docker/mysql/init/01_template_db.sql`
- Create: `docker/mysql/init/02_hospital_created.sql`

- [ ] **Step 1: 编写 base model**

`backend/app/models/base.py`:
```python
from datetime import datetime
from sqlalchemy import Column, BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
```

- [ ] **Step 2: 编写 database.py**

`backend/app/core/database.py`:
```python
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from typing import Dict, Generator

from app.config import settings


DATABASE_URL_TEMPLATE = (
    f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
    f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}"
)


_engines: Dict[str, "Engine"] = {}
_SessionLocals: Dict[str, sessionmaker] = {}


def _build_engine(db_name: str):
    url = f"{DATABASE_URL_TEMPLATE}/{db_name}?charset=utf8mb4"
    engine = create_engine(url, poolclass=QueuePool, pool_size=10, max_overflow=20, pool_pre_ping=True)
    return engine


def get_engine(db_name: str):
    if db_name not in _engines:
        _engines[db_name] = _build_engine(db_name)
    return _engines[db_name]


def get_session(db_name: str) -> Session:
    engine = get_engine(db_name)
    if db_name not in _SessionLocals:
        _SessionLocals[db_name] = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return _SessionLocals[db_name]()


def get_template_db() -> Generator[Session, None, None]:
    """获取模板库 session（平台级配置表）"""
    db = get_session(settings.MYSQL_TEMPLATE_DB)
    try:
        yield db
    finally:
        db.close()


def get_hospital_db(hospital_id: str) -> Generator[Session, None, None]:
    """获取医院数据库 session"""
    db_name = f"hospital_{hospital_id}"
    db = get_session(db_name)
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 3: 编写模板库建表 SQL**

`docker/mysql/init/01_template_db.sql`:
```sql
CREATE DATABASE IF NOT EXISTS hospital_template
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE hospital_template;

-- 医院租户注册表（平台超管管理）
CREATE TABLE IF NOT EXISTS hospital_tenant (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    hospital_id VARCHAR(32) NOT NULL UNIQUE COMMENT '医院唯一标识',
    hospital_name VARCHAR(100) NOT NULL COMMENT '医院名称',
    db_name VARCHAR(64) NOT NULL COMMENT '对应数据库名',
    is_active TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 平台用户表（三端共享认证）
CREATE TABLE IF NOT EXISTS platform_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL COMMENT 'user / doctor / admin',
    hospital_id VARCHAR(32) DEFAULT NULL COMMENT '医生/用户关联的医院',
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
```

- [ ] **Step 4: 编写医院库创建存储过程**

`docker/mysql/init/02_hospital_created.sql`:
```sql
USE hospital_template;

DELIMITER //

CREATE PROCEDURE IF NOT EXISTS create_hospital_database(IN p_hospital_id VARCHAR(32))
BEGIN
    SET @db_name = CONCAT('hospital_', p_hospital_id);
    SET @sql = CONCAT('CREATE DATABASE IF NOT EXISTS `', @db_name, '` DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci');
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('USE `', @db_name, '`');
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;

    -- 医院端用户表
    CREATE TABLE IF NOT EXISTS hospital_user (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id BIGINT NOT NULL COMMENT '关联 platform_user.id',
        name VARCHAR(50) DEFAULT NULL,
        phone VARCHAR(20) DEFAULT NULL,
        gender VARCHAR(5) DEFAULT NULL,
        age INT DEFAULT NULL,
        unit_name VARCHAR(100) DEFAULT NULL COMMENT '所属单位',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    -- 知识分类表
    CREATE TABLE IF NOT EXISTS knowledge_category (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        parent_id BIGINT DEFAULT NULL,
        sort_order INT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    -- 知识条目表
    CREATE TABLE IF NOT EXISTS knowledge_entry (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        category_id BIGINT DEFAULT NULL,
        title VARCHAR(200) NOT NULL,
        content TEXT NOT NULL,
        source_type VARCHAR(20) NOT NULL DEFAULT 'manual',
        source_file VARCHAR(500) DEFAULT NULL,
        chunk_index INT NOT NULL DEFAULT 0,
        parent_entry_id BIGINT DEFAULT NULL,
        vector_id VARCHAR(64) DEFAULT NULL,
        status TINYINT NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    -- 报告任务表
    CREATE TABLE IF NOT EXISTS report_task (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        original_file_path VARCHAR(500) NOT NULL,
        original_filename VARCHAR(200) NOT NULL,
        file_type VARCHAR(10) NOT NULL,
        file_size BIGINT NOT NULL DEFAULT 0,
        thumbnail_path VARCHAR(500) DEFAULT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'queued',
        priority TINYINT NOT NULL DEFAULT 0,
        retry_count INT NOT NULL DEFAULT 0,
        error_message TEXT DEFAULT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        completed_at DATETIME DEFAULT NULL
    ) ENGINE=InnoDB;

    -- 报告基本信息表
    CREATE TABLE IF NOT EXISTS report_info (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        task_id BIGINT DEFAULT NULL,
        user_id BIGINT NOT NULL,
        name VARCHAR(50) DEFAULT NULL,
        gender VARCHAR(5) DEFAULT NULL,
        age INT DEFAULT NULL,
        report_date DATE DEFAULT NULL,
        check_type VARCHAR(20) DEFAULT NULL,
        unit_name VARCHAR(100) DEFAULT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    -- 报告指标明细表
    CREATE TABLE IF NOT EXISTS report_indicator (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        report_id BIGINT NOT NULL,
        item_name VARCHAR(100) NOT NULL,
        item_name_standard VARCHAR(100) DEFAULT NULL,
        item_code VARCHAR(50) DEFAULT NULL,
        result_value VARCHAR(50) DEFAULT NULL,
        unit VARCHAR(20) DEFAULT NULL,
        ref_range_low VARCHAR(50) DEFAULT NULL,
        ref_range_high VARCHAR(50) DEFAULT NULL,
        category VARCHAR(50) DEFAULT NULL,
        raw_text TEXT DEFAULT NULL
    ) ENGINE=InnoDB;

    -- 解读结果表
    CREATE TABLE IF NOT EXISTS report_interpretation (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        report_id BIGINT NOT NULL,
        overall_level VARCHAR(10) DEFAULT NULL,
        red_count INT NOT NULL DEFAULT 0,
        yellow_count INT NOT NULL DEFAULT 0,
        green_count INT NOT NULL DEFAULT 0,
        summary_text TEXT DEFAULT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        retry_count INT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME DEFAULT NULL
    ) ENGINE=InnoDB;

    -- 指标研判明细表
    CREATE TABLE IF NOT EXISTS indicator_judgment (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        interpretation_id BIGINT NOT NULL,
        indicator_id BIGINT NOT NULL,
        item_name VARCHAR(100) NOT NULL,
        result_value VARCHAR(50) DEFAULT NULL,
        deviation VARCHAR(10) DEFAULT NULL,
        color_level VARCHAR(10) DEFAULT NULL,
        matched_rule_id BIGINT DEFAULT NULL,
        explanation TEXT DEFAULT NULL,
        suggestion TEXT DEFAULT NULL,
        knowledge_refs JSON DEFAULT NULL
    ) ENGINE=InnoDB;

    -- 三色规则表
    CREATE TABLE IF NOT EXISTS triage_rule (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        rule_name VARCHAR(100) NOT NULL,
        rule_type VARCHAR(20) NOT NULL,
        indicator_code VARCHAR(50) DEFAULT NULL,
        conditions JSON NOT NULL,
        color_level VARCHAR(10) NOT NULL,
        priority INT NOT NULL DEFAULT 0,
        is_active TINYINT NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    -- 报表模板表
    CREATE TABLE IF NOT EXISTS report_template (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        type VARCHAR(10) NOT NULL,
        content LONGBLOB DEFAULT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    -- 统计缓存表
    CREATE TABLE IF NOT EXISTS statistic_cache (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stat_type VARCHAR(50) NOT NULL,
        params_hash VARCHAR(64) NOT NULL,
        result_json JSON DEFAULT NULL,
        expired_at DATETIME DEFAULT NULL
    ) ENGINE=InnoDB;

    -- 调度配置表
    CREATE TABLE IF NOT EXISTS dispatch_config (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        config_key VARCHAR(50) NOT NULL,
        config_value VARCHAR(500) NOT NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    -- 资源监控表
    CREATE TABLE IF NOT EXISTS resource_metric (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        metric_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        cpu_percent DECIMAL(5,1) DEFAULT NULL,
        memory_percent DECIMAL(5,1) DEFAULT NULL,
        gpu_percent DECIMAL(5,1) DEFAULT NULL,
        gpu_memory_percent DECIMAL(5,1) DEFAULT NULL,
        queue_depth INT DEFAULT NULL,
        active_workers INT DEFAULT NULL
    ) ENGINE=InnoDB;
END//

DELIMITER ;
```

- [ ] **Step 5: 验证数据库连接**

Run: `python -c "from app.core.database import get_template_db; db = next(get_template_db()); print(db.execute(text('SELECT 1')).scalar())"`

Expected: 输出 `1`

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/ backend/app/models/ docker/mysql/
git commit -m "feat: add MySQL multi-db routing and schema"
```

---

### Task 3: JWT 认证服务

**Files:**
- Create: `backend/app/core/security.py`
- Create: `backend/app/core/dependencies.py`
- Create: `backend/app/middleware/__init__.py`
- Create: `backend/app/middleware/hospital_context.py`
- Create: `backend/app/api/auth.py`

- [ ] **Step 1: 编写 security.py**

`backend/app/core/security.py`:
```python
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
```

- [ ] **Step 2: 编写 dependencies.py**

`backend/app/core/dependencies.py`:
```python
from fastapi import Depends, Header
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_template_db, get_hospital_db
from app.core.security import decode_access_token
from app.utils.exceptions import UnauthorizedException, ForbiddenException


class CurrentUser:
    def __init__(self, user_id: int, role: str, hospital_id: Optional[str] = None):
        self.user_id = user_id
        self.role = role
        self.hospital_id = hospital_id


async def get_current_user(
    authorization: str = Header(..., description="Bearer <token>"),
    db: Session = Depends(get_template_db),
) -> CurrentUser:
    if not authorization.startswith("Bearer "):
        raise UnauthorizedException(detail="Invalid authorization header")
    token = authorization[7:]
    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedException(detail="Invalid or expired token")
    user_id = payload.get("user_id")
    role = payload.get("role")
    hospital_id = payload.get("hospital_id")
    if not user_id or not role:
        raise UnauthorizedException(detail="Invalid token payload")
    return CurrentUser(user_id=user_id, role=role, hospital_id=hospital_id)


def require_role(*roles: str):
    async def dependency(current_user: CurrentUser = Depends(get_current_user)):
        if current_user.role not in roles:
            raise ForbiddenException(detail=f"Requires role: {roles}")
        return current_user
    return dependency
```

- [ ] **Step 3: 编写 hospital_context 中间件**

`backend/app/middleware/hospital_context.py`:
```python
from contextvars import ContextVar
from typing import Optional

current_hospital_id: ContextVar[Optional[str]] = ContextVar("current_hospital_id", default=None)


def set_current_hospital_id(hospital_id: str):
    current_hospital_id.set(hospital_id)


def get_current_hospital_id() -> Optional[str]:
    return current_hospital_id.get()
```

- [ ] **Step 4: 编写认证 API**

`backend/app/api/auth.py`:
```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_template_db
from app.core.security import hash_password, verify_password, create_access_token
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import UnauthorizedException, ValidationException

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str
    hospital_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    role: str
    hospital_id: str | None = None


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_template_db)):
    row = db.execute(
        text("SELECT id, password_hash, role, hospital_id FROM platform_user WHERE username = :un AND is_active = 1"),
        {"un": req.username},
    ).fetchone()

    if not row or not verify_password(req.password, row.password_hash):
        raise UnauthorizedException(detail="Invalid username or password")

    token = create_access_token(data={
        "user_id": row.id,
        "role": row.role,
        "hospital_id": row.hospital_id,
    })
    return TokenResponse(
        access_token=token,
        user_id=row.id,
        role=row.role,
        hospital_id=row.hospital_id,
    )


@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_template_db)):
    if req.role not in ("user", "doctor", "admin"):
        raise ValidationException(detail="Invalid role")

    existing = db.execute(
        text("SELECT id FROM platform_user WHERE username = :un"), {"un": req.username}
    ).fetchone()
    if existing:
        raise ValidationException(detail="Username already exists")

    db.execute(
        text("INSERT INTO platform_user (username, password_hash, role, hospital_id) VALUES (:un, :ph, :r, :hid)"),
        {"un": req.username, "ph": hash_password(req.password), "r": req.role, "hid": req.hospital_id},
    )
    db.commit()

    row = db.execute(
        text("SELECT id, role, hospital_id FROM platform_user WHERE username = :un"),
        {"un": req.username},
    ).fetchone()

    token = create_access_token(data={
        "user_id": row.id,
        "role": row.role,
        "hospital_id": row.hospital_id,
    })
    return TokenResponse(
        access_token=token,
        user_id=row.id,
        role=row.role,
        hospital_id=row.hospital_id,
    )


@router.get("/me", response_model=TokenResponse)
def me(current_user: CurrentUser = Depends(get_current_user)):
    return TokenResponse(
        access_token="",
        user_id=current_user.user_id,
        role=current_user.role,
        hospital_id=current_user.hospital_id,
    )
```

- [ ] **Step 5: 验证认证流程**

Run:
```bash
# 注册
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"test_doctor","password":"123456","role":"doctor","hospital_id":"H001"}'

# 登录
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test_doctor","password":"123456"}'
```

Expected: 返回 access_token

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/security.py backend/app/core/dependencies.py backend/app/middleware/ backend/app/api/auth.py
git commit -m "feat: add JWT authentication service"
```

---

### Task 4: RabbitMQ 基础设施

**Files:**
- Create: `backend/app/core/rabbitmq.py`
- Create: `docker/rabbitmq/definitions.json`

- [ ] **Step 1: 编写 RabbitMQ 客户端封装**

`backend/app/core/rabbitmq.py`:
```python
import json
import pika
from typing import Callable, Optional
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class TaskMessage:
    task_type: str
    hospital_id: str
    priority: int = 0
    payload: dict = field(default_factory=dict)


class RabbitMQClient:
    EXCHANGE = "hospital.tasks"
    QUEUES = {
        "parsing.urgent": "parsing.urgent",
        "parsing.normal": "parsing.normal",
        "interpretation.urgent": "interpretation.urgent",
        "interpretation.normal": "interpretation.normal",
    }
    DEAD_LETTER_QUEUE = "dead.letter"

    def __init__(self):
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.channel.Channel] = None

    def _connect(self):
        credentials = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
        params = pika.ConnectionParameters(host=settings.RABBITMQ_HOST, port=settings.RABBITMQ_PORT, credentials=credentials)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()

    def _ensure_resources(self):
        self.channel.exchange_declare(exchange=self.EXCHANGE, exchange_type="topic", durable=True)
        for queue in self.QUEUES.values():
            self.channel.queue_declare(queue=queue, durable=True)
            routing_key = queue
            self.channel.queue_bind(exchange=self.EXCHANGE, queue=queue, routing_key=routing_key)
        self.channel.queue_declare(queue=self.DEAD_LETTER_QUEUE, durable=True)

    def publish(self, task: TaskMessage):
        if not self.connection or self.connection.is_closed:
            self._connect()
            self._ensure_resources()
        routing_key = f"{task.task_type}.{'urgent' if task.priority else 'normal'}"
        self.channel.basic_publish(
            exchange=self.EXCHANGE,
            routing_key=routing_key,
            body=json.dumps({"task_type": task.task_type, "hospital_id": task.hospital_id, "payload": task.payload}),
            properties=pika.BasicProperties(delivery_mode=2),
        )

    def consume(self, queue: str, callback: Callable, prefetch_count: int = 1):
        if not self.connection or self.connection.is_closed:
            self._connect()
            self._ensure_resources()
        self.channel.basic_qos(prefetch_count=prefetch_count)

        def _callback(ch, method, properties, body):
            try:
                message = json.loads(body)
                callback(message)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        self.channel.basic_consume(queue=queue, on_message_callback=_callback)

    def start_consuming(self):
        self.channel.start_consuming()

    def close(self):
        if self.connection and self.connection.is_open:
            self.connection.close()


rabbitmq = RabbitMQClient()
```

- [ ] **Step 2: 编写 RabbitMQ 预定义配置文件**

`docker/rabbitmq/definitions.json`:
```json
{
  "rabbit_version": "3.12",
  "users": [
    {"name": "guest", "password_hash": "guest", "tags": "administrator"}
  ],
  "vhosts": [{"name": "/"}],
  "permissions": [
    {"user": "guest", "vhost": "/", "configure": ".*", "write": ".*", "read": ".*"}
  ],
  "exchanges": [
    {"name": "hospital.tasks", "vhost": "/", "type": "topic", "durable": true}
  ],
  "queues": [
    {"name": "parsing.urgent", "vhost": "/", "durable": true},
    {"name": "parsing.normal", "vhost": "/", "durable": true},
    {"name": "interpretation.urgent", "vhost": "/", "durable": true},
    {"name": "interpretation.normal", "vhost": "/", "durable": true},
    {"name": "dead.letter", "vhost": "/", "durable": true}
  ],
  "bindings": [
    {"source": "hospital.tasks", "vhost": "/", "destination": "parsing.urgent", "routing_key": "parsing.urgent"},
    {"source": "hospital.tasks", "vhost": "/", "destination": "parsing.normal", "routing_key": "parsing.normal"},
    {"source": "hospital.tasks", "vhost": "/", "destination": "interpretation.urgent", "routing_key": "interpretation.urgent"},
    {"source": "hospital.tasks", "vhost": "/", "destination": "interpretation.normal", "routing_key": "interpretation.normal"}
  ]
}
```

- [ ] **Step 3: 验证 RabbitMQ 连接**

Run: `python -c "from app.core.rabbitmq import rabbitmq, TaskMessage; rabbitmq.publish(TaskMessage(task_type='parsing', hospital_id='H001', payload={'test': True})); print('Publish OK')"`

Expected: `Publish OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/rabbitmq.py docker/rabbitmq/
git commit -m "feat: add RabbitMQ client and queue definitions"
```

---

### Task 5: Milvus 向量数据库基础设施

**Files:**
- Create: `backend/app/core/milvus.py`

- [ ] **Step 1: 编写 Milvus 客户端封装**

`backend/app/core/milvus.py`:
```python
from typing import List, Dict, Optional
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility

from app.config import settings


COLLECTION_TEMPLATE = "hospital_{hospital_id}_knowledge"
VECTOR_DIM = 1024


class MilvusClient:
    def __init__(self):
        self._connected = False

    def _ensure_connection(self):
        if not self._connected:
            connections.connect(host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
            self._connected = True

    def get_collection_name(self, hospital_id: str) -> str:
        return COLLECTION_TEMPLATE.format(hospital_id=hospital_id)

    def create_collection(self, hospital_id: str):
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        if utility.has_collection(collection_name):
            return

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
            FieldSchema(name="entry_id", dtype=DataType.INT64),
            FieldSchema(name="category_id", dtype=DataType.INT64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=200),
            FieldSchema(name="source_file", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="created_at", dtype=DataType.INT64),
        ]
        schema = CollectionSchema(fields, description=f"Knowledge vectors for hospital {hospital_id}")
        collection = Collection(name=collection_name, schema=schema)

        index_params = {
            "metric_type": "IP",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        collection.create_index(field_name="vector", index_params=index_params)
        collection.load()

    def drop_collection(self, hospital_id: str):
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        if utility.has_collection(collection_name):
            utility.drop_collection(collection_name)

    def insert(self, hospital_id: str, vectors: List[List[float]], metadata: List[dict]):
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        collection = Collection(name=collection_name)
        data = [
            [v for v in vectors],
            [m["entry_id"] for m in metadata],
            [m.get("category_id", 0) for m in metadata],
            [m.get("title", "") for m in metadata],
            [m.get("source_file", "") for m in metadata],
            [m.get("created_at", 0) for m in metadata],
        ]
        collection.insert(data)

    def search(
        self,
        hospital_id: str,
        query_vector: List[float],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> List[Dict]:
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        collection = Collection(name=collection_name)
        search_params = {"metric_type": "IP", "params": {"nprobe": 16}}
        results = collection.search(
            data=[query_vector],
            anns_field="vector",
            param=search_params,
            limit=top_k,
            expr=filter_expr,
            output_fields=["entry_id", "category_id", "title", "source_file"],
        )
        out = []
        for hits in results:
            for hit in hits:
                out.append({
                    "entry_id": hit.entity.get("entry_id"),
                    "category_id": hit.entity.get("category_id"),
                    "title": hit.entity.get("title"),
                    "source_file": hit.entity.get("source_file"),
                    "score": hit.score,
                })
        return out

    def delete_by_ids(self, hospital_id: str, ids: List[int]):
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        collection = Collection(name=collection_name)
        collection.delete(expr=f"entry_id in {ids}")

    def delete_by_criteria(self, hospital_id: str, expr: str):
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        collection = Collection(name=collection_name)
        collection.delete(expr=expr)

    def flush(self, hospital_id: str):
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        Collection(name=collection_name).flush()


milvus_client = MilvusClient()
```

- [ ] **Step 2: 验证 Milvus 连接**

Run: `python -c "from app.core.milvus import milvus_client; milvus_client.create_collection('H001'); print('Milvus OK')"`

Expected: `Milvus OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/milvus.py
git commit -m "feat: add Milvus client with namespace-based isolation"
```

---

### Task 6: Docker Compose 开发环境

**Files:**
- Create: `docker/docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: 编写 docker-compose.yml**

`docker/docker-compose.yml`:
```yaml
version: "3.8"

services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: root123
      MYSQL_CHARACTER_SET_SERVER: utf8mb4
      MYSQL_COLLATION_SERVER: utf8mb4_unicode_ci
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql
      - ./mysql/init:/docker-entrypoint-initdb.d

  rabbitmq:
    image: rabbitmq:3.12-management
    ports:
      - "5672:5672"
      - "15672:15672"
    environment:
      RABBITMQ_DEFAULT_USER: guest
      RABBITMQ_DEFAULT_PASS: guest
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
      - ./rabbitmq/definitions.json:/etc/rabbitmq/definitions.json

  etcd:
    image: quay.io/coreos/etcd:v3.5.5
    environment:
      ETCD_AUTO_COMPACTION_MODE: revision
      ETCD_AUTO_COMPACTION_RETENTION: "1000"
      ETCD_QUOTA_BACKEND_BYTES: "4294967296"
      ETCD_SNAPSHOT_COUNT: "50000"
    command: etcd -advertise-client-urls=http://etcd:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd

  milvus:
    image: milvusdb/milvus:v2.5.0
    depends_on:
      - etcd
    ports:
      - "19530:19530"
      - "9091:9091"
    environment:
      ETCD_ENDPOINTS: etcd:2379
    volumes:
      - milvus_data:/var/lib/milvus

volumes:
  mysql_data:
  rabbitmq_data:
  milvus_data:
```

- [ ] **Step 2: 编写 .env.example**

```
# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=root123

# Milvus
MILVUS_HOST=localhost
MILVUS_PORT=19530

# RabbitMQ
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# JWT
SECRET_KEY=dev-secret-key-change-in-production

# File Storage
FILE_STORAGE_ROOT=./storage

# App
DEBUG=true
```

- [ ] **Step 3: 启动开发环境**

Run: `cd docker && docker-compose up -d`

Expected: 所有服务启动成功（mysql, rabbitmq, etcd, milvus）

- [ ] **Step 4: 验证全部服务**

Run:
```bash
# MySQL
docker exec -it docker-mysql-1 mysql -uroot -proot123 -e "SHOW DATABASES;"

# RabbitMQ
curl http://localhost:15672 (访问管理界面)

# Milvus
python -c "from pymilvus import connections; connections.connect(host='localhost', port='19530'); print('OK')"
```

Expected: 全部 OK

- [ ] **Step 5: Commit**

```bash
git add docker/docker-compose.yml .env.example
git commit -m "feat: add Docker Compose development environment"
```

---

### Task 7: 前端项目骨架 (三端)

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/packages/shared/package.json`
- Create: `frontend/packages/shared/tsconfig.json`
- Create: `frontend/packages/shared/src/index.ts`
- Create: `frontend/packages/shared/src/api/client.ts`
- Create: `frontend/packages/user-portal/package.json`
- Create: `frontend/packages/user-portal/vite.config.ts`
- Create: `frontend/packages/user-portal/tsconfig.json`
- Create: `frontend/packages/user-portal/index.html`
- Create: `frontend/packages/user-portal/src/main.tsx`
- Create: `frontend/packages/user-portal/src/App.tsx`
- Create: `frontend/packages/user-portal/src/router.tsx`
- Create: `frontend/packages/user-portal/src/pages/HomePage.tsx`
- Create: `frontend/packages/doctor-portal/package.json`
- Create: `frontend/packages/doctor-portal/vite.config.ts`
- Create: `frontend/packages/doctor-portal/tsconfig.json`
- Create: `frontend/packages/doctor-portal/index.html`
- Create: `frontend/packages/doctor-portal/src/main.tsx`
- Create: `frontend/packages/doctor-portal/src/App.tsx`
- Create: `frontend/packages/doctor-portal/src/router.tsx`
- Create: `frontend/packages/doctor-portal/src/pages/DashboardPage.tsx`
- Create: `frontend/packages/admin-portal/package.json`
- Create: `frontend/packages/admin-portal/vite.config.ts`
- Create: `frontend/packages/admin-portal/tsconfig.json`
- Create: `frontend/packages/admin-portal/index.html`
- Create: `frontend/packages/admin-portal/src/main.tsx`
- Create: `frontend/packages/admin-portal/src/App.tsx`
- Create: `frontend/packages/admin-portal/src/router.tsx`
- Create: `frontend/packages/admin-portal/src/pages/PlatformDashboard.tsx`

- [ ] **Step 1: 编写根 package.json**

`frontend/package.json`:
```json
{
  "name": "hospital-ai-frontend",
  "private": true,
  "workspaces": [
    "packages/shared",
    "packages/user-portal",
    "packages/doctor-portal",
    "packages/admin-portal"
  ]
}
```

- [ ] **Step 2: 编写共享组件库骨架**

`frontend/packages/shared/package.json`:
```json
{
  "name": "@hospital/shared",
  "version": "0.1.0",
  "main": "src/index.ts",
  "dependencies": {
    "axios": "^1.7.9"
  }
}
```

`frontend/packages/shared/src/api/client.ts`:
```typescript
import axios, { AxiosInstance, AxiosRequestConfig } from "axios";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api/v1";

export const createApiClient = (getToken: () => string | null): AxiosInstance => {
  const client = axios.create({ baseURL: BASE_URL });

  client.interceptors.request.use((config) => {
    const token = getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  client.interceptors.response.use(
    (response) => response,
    (error) => {
      if (error.response?.status === 401) {
        localStorage.removeItem("token");
        window.location.href = "/login";
      }
      return Promise.reject(error);
    }
  );

  return client;
};
```

`frontend/packages/shared/src/index.ts`:
```typescript
export { createApiClient } from "./api/client";
```

- [ ] **Step 3: 编写用户端骨架**

`frontend/packages/user-portal/package.json`:
```json
{
  "name": "@hospital/user-portal",
  "version": "0.1.0",
  "scripts": {
    "dev": "vite --port 3001",
    "build": "tsc && vite build"
  },
  "dependencies": {
    "@hospital/shared": "*",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "antd": "^5.22.0",
    "zustand": "^5.0.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.6.0",
    "vite": "^6.0.0"
  }
}
```

`frontend/packages/user-portal/vite.config.ts`:
```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 3001 },
});
```

`frontend/packages/user-portal/index.html`:
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>体检报告查询</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/src/main.tsx"></script>
</body>
</html>
```

`frontend/packages/user-portal/src/main.tsx`:
```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>
);
```

`frontend/packages/user-portal/src/App.tsx`:
```tsx
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { AppRouter } from "./router";

export const App = () => (
  <ConfigProvider locale={zhCN}>
    <BrowserRouter>
      <AppRouter />
    </BrowserRouter>
  </ConfigProvider>
);
```

`frontend/packages/user-portal/src/router.tsx`:
```tsx
import { Routes, Route, Navigate } from "react-router-dom";
import HomePage from "./pages/HomePage";

export const AppRouter = () => (
  <Routes>
    <Route path="/" element={<HomePage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);
```

`frontend/packages/user-portal/src/pages/HomePage.tsx`:
```tsx
import { Typography } from "antd";

export default function HomePage() {
  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={3}>我的体检报告</Typography.Title>
      <Typography.Text type="secondary">用户端 — 开发中</Typography.Text>
    </div>
  );
}
```

- [ ] **Step 4: 编写医生端骨架（同结构，端口 3002，标题"医生工作台"）**

与用户端结构一致，差异点：
- `package.json`: name 为 `@hospital/doctor-portal`
- `vite.config.ts`: port 3002
- `index.html`: title 为"医生工作台"
- `router.tsx`: 首页指向 `DashboardPage`
- `DashboardPage.tsx`: 标题为"医生工作台"

- [ ] **Step 5: 编写管理后台骨架（同结构，端口 3003，标题"平台管理"）**

与用户端结构一致，差异点：
- `package.json`: name 为 `@hospital/admin-portal`
- `vite.config.ts`: port 3003
- `index.html`: title 为"平台管理"
- `router.tsx`: 首页指向 `PlatformDashboard`
- `PlatformDashboard.tsx`: 标题为"平台管理后台"

- [ ] **Step 6: 安装依赖并验证**

Run: `cd frontend && npm install && npm run dev -w @hospital/user-portal`

Expected: `http://localhost:3001` 显示"我的体检报告 — 用户端 — 开发中"

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: add frontend project skeletons for three portals"
```

---

### Task 8: APISIX 网关配置

**Files:**
- Create: `apisix/config.yml`

- [ ] **Step 1: 编写 APISIX 配置**

`apisix/config.yml`:
```yaml
routes:
  - id: backend-api
    uri: /api/v1/*
    upstream:
      type: roundrobin
      nodes:
        "backend:8000": 1
    plugins:
      cors:
        allow_origins: "*"
        allow_methods: "GET,POST,PUT,DELETE"
        allow_headers: "Authorization,Content-Type"
      limit-req:
        rate: 100
        burst: 50
        key: remote_addr

  - id: user-portal
    uri: /*
    upstream:
      type: roundrobin
      nodes:
        "user-portal:3001": 1
    plugins:
      redirect:
        regex_uri: ["^/doctor.*", "/doctor/", 302]

upstreams:
  - id: backend
    nodes:
      "backend:8000": 1
```

- [ ] **Step 2: Commit**

```bash
git add apisix/
git commit -m "feat: add APISIX gateway configuration"
```

---

### Task 9: 项目根目录文件

**Files:**
- Create: `.gitignore`

- [ ] **Step 1: 编写 .gitignore**

```
# Python
__pycache__/
*.pyc
.venv/
venv/

# Node
node_modules/
dist/

# Environment
.env
!.env.example

# Storage
storage/

# IDE
.idea/
.vscode/
*.swp

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: add .gitignore"
```
