# 检后随访(随访问卷 + 需复查提醒,兼容外部 App)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 报告解读完成后自动对红/黄报告生成随访问卷(快照自平台单套激活模板)与一条站内复查提醒,用户(外部 App / user-portal)自填问卷、接口轮询通知;医生可查答卷,平台管理员维护模板。

**Architecture:** 后端新增 `app/modules/followup` 模块(平台模板模型 + 租户随访/答卷/通知模型)。生成时机挂在 `interpretation/worker.py` 成功分支(方案 A 同步、幂等 UNIQUE(report_id)、失败只记日志)。用户侧接口全部 `role='user'` + 双锚定 `(user_id=后六位, name)` → app-login JWT 零改动可用。前端:user-portal 加第 4 个「随访」tab + 随访中心/问卷页(参考实现),doctor-portal 报告详情加随访区块,admin-portal 加模板维护页。DB 层:平台库 2 张模板表 + 每租户 3 张表,三处 DDL(01_template_db.sql / start.sh / 02_hospital_created.sql)+ `006_followup.sql` 存量迁移。

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy 2 / pytest(sqlite in-memory)/ React 18 + AntD 5 + zustand + react-router / TypeScript 5。

## Global Constraints

- 后端测试从 `backend/` 运行,解释器 `backend/.venv/bin/python`:`cd backend && .venv/bin/python -m pytest -q tests/...`
- 前端类型检查在 `frontend/` 运行:`pnpm exec tsc -p packages/<pkg>/tsconfig.json --noEmit`;仓库无 jest/vitest,前端不做单测
- 用户侧接口必须 `role='user'` + `user_identity()` 双锚定;存量 user 无后六位 → 返回空集,不 500/401
- 不加 vllm、不改 backend/pyproject.toml 依赖、不新增 worker 进程/队列;新 logger 用 `app.followup`
- 新表在 4 处保持一致:平台库 `followup_template`/`followup_template_question`;租户库 `followup`/`followup_question`/`user_notification`
- 快照原则:生成时把激活模板问题与黄+红指标拷入租户库,改模板不影响已发问卷
- 提交即 `completed`,不可重复提交;通知与问卷同生同亡于同一事务
- MySQL JSON 列在 ORM 里直接赋 Python 对象;SQLite 测试用 in-memory + BigInteger→INTEGER compile hack(照抄 `tests/user_profile/conftest.py`)

---

### Task 1: DDL —— 平台库模板表 + 租户库三表(4 处落地)

**Files:**
- Modify: `infra/mysql/init/01_template_db.sql`(末尾追加 2 张平台模板表)
- Modify: `start.sh:159`(hospital_H001 DDL heredoc 末尾、`SQL` 行前追加 3 张租户表)
- Modify: `infra/mysql/init/02_hospital_created.sql:191`(`END//` 前、batch_import_file 之后追加 3 张租户表)
- Create: `backend/scripts/manual_migrations/006_followup.sql`
- Create: `backend/tests/followup/__init__.py`、`backend/tests/followup/conftest.py`、`backend/tests/followup/test_ddl_sync.py`

**Interfaces:** Produces DDL 文本;后续 Task 3-5 的 ORM 模型列名与此处 SQL 完全一致。

- [ ] **Step 1: 追加平台库模板表**

追加到 `infra/mysql/init/01_template_db.sql` 末尾(保持 `--` 注释风格):

```sql
-- 检后随访:平台统一维护的单套激活问卷模板(跨所有医院共享)
CREATE TABLE IF NOT EXISTS followup_template (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL COMMENT '模板名(如「通用检后随访」)',
    description VARCHAR(500) DEFAULT NULL,
    is_active TINYINT NOT NULL DEFAULT 1 COMMENT '同一时刻仅 1 套激活',
    updated_by BIGINT DEFAULT NULL COMMENT '维护人 platform_user.id',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS followup_template_question (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    template_id BIGINT NOT NULL,
    question_type VARCHAR(10) NOT NULL COMMENT 'single / multiple / text',
    question_text VARCHAR(500) NOT NULL,
    options JSON DEFAULT NULL COMMENT 'single/multiple 的选项数组',
    is_required TINYINT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: start.sh 租户库三表**

在 `start.sh` 第 159 行 `batch_import_file ...` 之后、第 160 行 `SQL` 之前,插入三行(整行追加,`ENGINE=InnoDB DEFAULT CHARSET=utf8mb4` 结尾):

```
CREATE TABLE IF NOT EXISTS followup (id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, user_id VARCHAR(16) NOT NULL, name VARCHAR(50), overall_level VARCHAR(10) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending', recheck_indicators_json JSON DEFAULT NULL, template_name VARCHAR(100), generated_at DATETIME DEFAULT CURRENT_TIMESTAMP, submitted_at DATETIME DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq_followup_report (report_id), KEY idx_followup_user (user_id, name)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS followup_question (id BIGINT AUTO_INCREMENT PRIMARY KEY, followup_id BIGINT NOT NULL, question_type VARCHAR(10) NOT NULL, question_text VARCHAR(500) NOT NULL, options JSON DEFAULT NULL, is_required TINYINT NOT NULL DEFAULT 1, sort_order INT NOT NULL DEFAULT 0, answer TEXT DEFAULT NULL, answered_at DATETIME DEFAULT NULL, KEY idx_fq_followup (followup_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS user_notification (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, name VARCHAR(50), category VARCHAR(24) NOT NULL, title VARCHAR(200) NOT NULL, content JSON NOT NULL, ref_report_id BIGINT DEFAULT NULL, ref_followup_id BIGINT DEFAULT NULL, is_read TINYINT NOT NULL DEFAULT 0, read_at DATETIME DEFAULT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, KEY idx_un_user_created (user_id, name, created_at)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 3: 02_hospital_created.sql 存储过程追加**

在 `infra/mysql/init/02_hospital_created.sql` 第 191 行(`batch_import_file` 的 `PREPARE/EXECUTE` 之后)、`END//` 之前追加(沿用 `SET @sql = CONCAT('...', @db_name, '...')` 动态建表模式):

```sql
    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.followup ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, '
        'user_id VARCHAR(16) NOT NULL, name VARCHAR(50), '
        'overall_level VARCHAR(10) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT ''pending'', '
        'recheck_indicators_json JSON DEFAULT NULL, template_name VARCHAR(100), '
        'generated_at DATETIME DEFAULT CURRENT_TIMESTAMP, submitted_at DATETIME DEFAULT NULL, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'UNIQUE KEY uq_followup_report (report_id), KEY idx_followup_user (user_id, name)'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.followup_question ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, followup_id BIGINT NOT NULL, '
        'question_type VARCHAR(10) NOT NULL, question_text VARCHAR(500) NOT NULL, '
        'options JSON DEFAULT NULL, is_required TINYINT NOT NULL DEFAULT 1, '
        'sort_order INT NOT NULL DEFAULT 0, answer TEXT DEFAULT NULL, answered_at DATETIME DEFAULT NULL, '
        'KEY idx_fq_followup (followup_id)'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.user_notification ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, name VARCHAR(50), '
        'category VARCHAR(24) NOT NULL, title VARCHAR(200) NOT NULL, content JSON NOT NULL, '
        'ref_report_id BIGINT DEFAULT NULL, ref_followup_id BIGINT DEFAULT NULL, '
        'is_read TINYINT NOT NULL DEFAULT 0, read_at DATETIME DEFAULT NULL, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'KEY idx_un_user_created (user_id, name, created_at)'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
```

- [ ] **Step 4: 新建存量迁移脚本**

创建 `backend/scripts/manual_migrations/006_followup.sql`:

```sql
-- 006: 检后随访 —— 平台库 2 张模板表 + 每个存量 tenant 库 3 张表
-- 用法:mysql -uroot -proot --default-character-set=utf8mb4 < 006_followup.sql
--   (先建 hospital_template 平台模板表,再逐 tenant 库全限定建 3 张业务表)
-- 平台库模板表与 01_template_db.sql 一致;新 tenant 由 create_hospital_database 自动带出。

USE hospital_template;

CREATE TABLE IF NOT EXISTS followup_template (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL COMMENT '模板名(如「通用检后随访」)',
    description VARCHAR(500) DEFAULT NULL,
    is_active TINYINT NOT NULL DEFAULT 1 COMMENT '同一时刻仅 1 套激活',
    updated_by BIGINT DEFAULT NULL COMMENT '维护人 platform_user.id',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS followup_template_question (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    template_id BIGINT NOT NULL,
    question_type VARCHAR(10) NOT NULL COMMENT 'single / multiple / text',
    question_text VARCHAR(500) NOT NULL,
    options JSON DEFAULT NULL COMMENT 'single/multiple 的选项数组',
    is_required TINYINT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 下面 3 段是对每个存量 tenant 库的完整建表(hospital_template 里不建这 3 张业务表)。
-- MySQL 8 的 CREATE TABLE IF NOT EXISTS dst LIKE src 支持跨库 src(如
-- `LIKE hospital_template.followup`),但为自包含、可读,这里直接逐库重复完整 DDL。
CREATE TABLE IF NOT EXISTS hospital_H001.followup (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL,
    user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    overall_level VARCHAR(10) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending',
    recheck_indicators_json JSON DEFAULT NULL, template_name VARCHAR(100),
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP, submitted_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_followup_report (report_id), KEY idx_followup_user (user_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_H001.followup_question (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, followup_id BIGINT NOT NULL,
    question_type VARCHAR(10) NOT NULL, question_text VARCHAR(500) NOT NULL,
    options JSON DEFAULT NULL, is_required TINYINT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0, answer TEXT DEFAULT NULL, answered_at DATETIME DEFAULT NULL,
    KEY idx_fq_followup (followup_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_H001.user_notification (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    category VARCHAR(24) NOT NULL, title VARCHAR(200) NOT NULL, content JSON NOT NULL,
    ref_report_id BIGINT DEFAULT NULL, ref_followup_id BIGINT DEFAULT NULL,
    is_read TINYINT NOT NULL DEFAULT 0, read_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_un_user_created (user_id, name, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hospital_H002.followup (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL,
    user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    overall_level VARCHAR(10) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending',
    recheck_indicators_json JSON DEFAULT NULL, template_name VARCHAR(100),
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP, submitted_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_followup_report (report_id), KEY idx_followup_user (user_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_H002.followup_question (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, followup_id BIGINT NOT NULL,
    question_type VARCHAR(10) NOT NULL, question_text VARCHAR(500) NOT NULL,
    options JSON DEFAULT NULL, is_required TINYINT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0, answer TEXT DEFAULT NULL, answered_at DATETIME DEFAULT NULL,
    KEY idx_fq_followup (followup_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_H002.user_notification (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    category VARCHAR(24) NOT NULL, title VARCHAR(200) NOT NULL, content JSON NOT NULL,
    ref_report_id BIGINT DEFAULT NULL, ref_followup_id BIGINT DEFAULT NULL,
    is_read TINYINT NOT NULL DEFAULT 0, read_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_un_user_created (user_id, name, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hospital_H003.followup (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL,
    user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    overall_level VARCHAR(10) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending',
    recheck_indicators_json JSON DEFAULT NULL, template_name VARCHAR(100),
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP, submitted_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_followup_report (report_id), KEY idx_followup_user (user_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_H003.followup_question (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, followup_id BIGINT NOT NULL,
    question_type VARCHAR(10) NOT NULL, question_text VARCHAR(500) NOT NULL,
    options JSON DEFAULT NULL, is_required TINYINT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0, answer TEXT DEFAULT NULL, answered_at DATETIME DEFAULT NULL,
    KEY idx_fq_followup (followup_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_H003.user_notification (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    category VARCHAR(24) NOT NULL, title VARCHAR(200) NOT NULL, content JSON NOT NULL,
    ref_report_id BIGINT DEFAULT NULL, ref_followup_id BIGINT DEFAULT NULL,
    is_read TINYINT NOT NULL DEFAULT 0, read_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_un_user_created (user_id, name, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hospital_H004.followup (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL,
    user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    overall_level VARCHAR(10) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending',
    recheck_indicators_json JSON DEFAULT NULL, template_name VARCHAR(100),
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP, submitted_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_followup_report (report_id), KEY idx_followup_user (user_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_H004.followup_question (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, followup_id BIGINT NOT NULL,
    question_type VARCHAR(10) NOT NULL, question_text VARCHAR(500) NOT NULL,
    options JSON DEFAULT NULL, is_required TINYINT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0, answer TEXT DEFAULT NULL, answered_at DATETIME DEFAULT NULL,
    KEY idx_fq_followup (followup_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_H004.user_notification (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    category VARCHAR(24) NOT NULL, title VARCHAR(200) NOT NULL, content JSON NOT NULL,
    ref_report_id BIGINT DEFAULT NULL, ref_followup_id BIGINT DEFAULT NULL,
    is_read TINYINT NOT NULL DEFAULT 0, read_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_un_user_created (user_id, name, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hospital_1.followup (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL,
    user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    overall_level VARCHAR(10) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending',
    recheck_indicators_json JSON DEFAULT NULL, template_name VARCHAR(100),
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP, submitted_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_followup_report (report_id), KEY idx_followup_user (user_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_1.followup_question (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, followup_id BIGINT NOT NULL,
    question_type VARCHAR(10) NOT NULL, question_text VARCHAR(500) NOT NULL,
    options JSON DEFAULT NULL, is_required TINYINT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0, answer TEXT DEFAULT NULL, answered_at DATETIME DEFAULT NULL,
    KEY idx_fq_followup (followup_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS hospital_1.user_notification (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(16) NOT NULL, name VARCHAR(50),
    category VARCHAR(24) NOT NULL, title VARCHAR(200) NOT NULL, content JSON NOT NULL,
    ref_report_id BIGINT DEFAULT NULL, ref_followup_id BIGINT DEFAULT NULL,
    is_read TINYINT NOT NULL DEFAULT 0, read_at DATETIME DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_un_user_created (user_id, name, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

> 注:平台库两张模板表在文件开头(默认库已 `USE hospital_template`),存量 tenant 库按
> `hospital_H001/H002/H003/H004/hospital_1` 各自全限定建表(与 start.sh/存储过程 DDL 一致)。

- [ ] **Step 5: 建测试目录与同步守卫测试**

创建 `backend/tests/followup/__init__.py`(空)、`backend/tests/followup/conftest.py`:

```python
"""Test infrastructure for followup tests — 复制 user_profile/conftest 的
BigInteger→INTEGER(SQLite autoincrement)hack。"""
from sqlalchemy import BigInteger
from sqlalchemy.ext.compiler import compiles


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(element, compiler, **kw):
    return compiler.visit_INTEGER(element, **kw)
```

创建 `backend/tests/followup/test_ddl_sync.py`:

```python
"""新表须在三处 DDL 源保持一致(AGENTS.md 纪律):01_template_db.sql(平台 2 张)、
start.sh heredoc(租户 3 张)、02_hospital_created.sql(租户 3 张)。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_TABLES = ["followup_template", "followup_template_question"]
TENANT_TABLES = ["followup", "followup_question", "user_notification"]


def test_platform_tables_in_01_template_db():
    txt = (ROOT / "infra/mysql/init/01_template_db.sql").read_text(encoding="utf-8")
    for t in TEMPLATE_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {t}" in txt


def test_tenant_tables_in_startsh_and_proc():
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    proc = (ROOT / "infra/mysql/init/02_hospital_created.sql").read_text(encoding="utf-8")
    for t in TENANT_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {t} " in start
        assert f"`.{t}`" in proc


def test_migration_006_covers_all_tenant_dbs():
    mig = (ROOT / "backend/scripts/manual_migrations/006_followup.sql").read_text(encoding="utf-8")
    for hid in ["hospital_H001", "hospital_H002", "hospital_H003", "hospital_H004", "hospital_1"]:
        assert hid in mig
```

- [ ] **Step 6: 运行 DDL 同步测试**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_ddl_sync.py`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add infra/mysql/init/01_template_db.sql start.sh infra/mysql/init/02_hospital_created.sql backend/scripts/manual_migrations/006_followup.sql backend/tests/followup
git commit -m "feat: 检后随访 DDL(平台模板 2 表 + 租户 3 表)三处落地 + 006 迁移"
```

---

### Task 2: followup 模块骨架(models + schemas)

**Files:**
- Create: `backend/app/modules/followup/__init__.py`(空)
- Create: `backend/app/modules/followup/models.py`
- Create: `backend/app/modules/followup/schemas.py`
- Test: `backend/tests/followup/test_models.py`

**Interfaces:**
- Produces ORM 模型类 `FollowupTemplate`/`FollowupTemplateQuestion`(平台库)、`Followup`/`FollowupQuestion`/`UserNotification`(租户库);Pydantic 请求模型 `SubmitRequest`/`AnswerItem`/`TemplateSaveRequest`/`TemplateQuestionItem`。后续所有任务 import 它们。

- [ ] **Step 1: 写失败测试(建表成功 + 约束存在)**

创建 `backend/tests/followup/test_models.py`:

```python
"""模型骨架:5 张表能创建,唯一约束在库层生效。"""
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.followup.models import (  # noqa: F401 — 注册到 Base.metadata
    FollowupTemplate, FollowupTemplateQuestion,
    Followup, FollowupQuestion, UserNotification,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def test_all_tables_created(db):
    names = set(inspect(db.get_bind()).get_table_names())
    assert {"followup_template", "followup_template_question",
            "followup", "followup_question", "user_notification"} <= names


def test_followup_report_unique(db):
    from sqlalchemy.exc import IntegrityError
    db.add(Followup(report_id=1, user_id="123456", name="张三", overall_level="red"))
    db.add(Followup(report_id=1, user_id="123456", name="张三", overall_level="red"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_models.py`
Expected: FAIL(`ModuleNotFoundError: app.modules.followup`)。

- [ ] **Step 3: 写 models.py**

创建 `backend/app/modules/followup/models.py`:

```python
from sqlalchemy import Column, BigInteger, String, Text, Integer, DateTime, JSON, UniqueConstraint, func
from app.models.base import Base


class FollowupTemplate(Base):
    """平台库 hospital_template:随访问卷模板(单套激活,平台管理员维护)。"""
    __tablename__ = "followup_template"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    updated_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class FollowupTemplateQuestion(Base):
    """平台库:模板问题(生成时快照进租户库 followup_question)。"""
    __tablename__ = "followup_template_question"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    template_id = Column(BigInteger, nullable=False)
    question_type = Column(String(10), nullable=False)
    question_text = Column(String(500), nullable=False)
    options = Column(JSON, nullable=True)
    is_required = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class Followup(Base):
    """租户库:每份红/黄报告一份随访实例。"""
    __tablename__ = "followup"
    __table_args__ = (UniqueConstraint("report_id", name="uq_followup_report"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, nullable=False)
    user_id = Column(String(16), nullable=False)
    name = Column(String(50), nullable=True)
    overall_level = Column(String(10), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    recheck_indicators_json = Column(JSON, nullable=True)
    template_name = Column(String(100), nullable=True)
    generated_at = Column(DateTime, default=func.now(), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class FollowupQuestion(Base):
    """租户库:题目快照 + 用户答案(单表)。"""
    __tablename__ = "followup_question"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    followup_id = Column(BigInteger, nullable=False)
    question_type = Column(String(10), nullable=False)
    question_text = Column(String(500), nullable=False)
    options = Column(JSON, nullable=True)
    is_required = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    answer = Column(Text, nullable=True)
    answered_at = Column(DateTime, nullable=True)


class UserNotification(Base):
    """租户库:站内通知(当前仅 recheck_reminder,预留 category 扩展)。"""
    __tablename__ = "user_notification"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(16), nullable=False)
    name = Column(String(50), nullable=True)
    category = Column(String(24), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(JSON, nullable=False)
    ref_report_id = Column(BigInteger, nullable=True)
    ref_followup_id = Column(BigInteger, nullable=True)
    is_read = Column(Integer, nullable=False, default=0)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
```

- [ ] **Step 4: 写 schemas.py**

创建 `backend/app/modules/followup/schemas.py`:

```python
from typing import Any, List, Optional
from pydantic import BaseModel


class AnswerItem(BaseModel):
    question_id: int
    answer: Any = None


class SubmitRequest(BaseModel):
    answers: List[AnswerItem]


class TemplateQuestionItem(BaseModel):
    id: Optional[int] = None  # 接受但不使用:PUT 为全量替换,历史 id 无跨库意义
    question_type: str
    question_text: str
    options: Optional[List[str]] = None
    is_required: bool = True
    sort_order: int = 0
    is_active: bool = True


class TemplateSaveRequest(BaseModel):
    name: str = "通用检后随访"
    description: Optional[str] = None
    questions: List[TemplateQuestionItem]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_models.py`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/followup backend/tests/followup/test_models.py
git commit -m "feat(followup): 模型与 schema 骨架(平台模板 + 租户随访/通知)"
```

---

### Task 3: 平台模板读写 service(get_active_template / save_active_template)

**Files:**
- Create: `backend/app/modules/followup/service.py`(本任务先实现模板部分;后续任务追加函数到同一文件)
- Test: `backend/tests/followup/test_service_template.py`

**Interfaces:**
- Produces `service.get_active_template(db: Session) -> dict`、`service.save_active_template(db, name, description, questions, updater_id=None) -> dict`。
- Consumes models 自 Task 2、`ValidationException` 自 `app.utils.exceptions`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/followup/test_service_template.py`:

```python
"""平台模板读写:空题拦截 / 新建激活 / 全量替换。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.followup.models import (  # noqa: F401
    FollowupTemplate, FollowupTemplateQuestion,
    Followup, FollowupQuestion, UserNotification,
)
from app.modules.followup import service
from app.utils.exceptions import ValidationException


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def test_get_empty_template(db):
    data = service.get_active_template(db)
    assert data["id"] is None
    assert data["questions"] == []


def test_save_rejects_empty_questions(db):
    with pytest.raises(ValidationException):
        service.save_active_template(db, "通用", None, [])


def test_save_rejects_bad_question_type(db):
    with pytest.raises(ValidationException):
        service.save_active_template(db, "通用", None,
                                     [{"question_type": "radio",
                                       "question_text": "x", "options": ["a"]}])


def test_save_creates_then_replaces(db):
    qs = [
        {"question_type": "single", "question_text": "近期是否头晕？",
         "options": ["经常", "偶尔", "无"], "is_required": True, "sort_order": 1},
        {"question_type": "text", "question_text": "补充说明",
         "is_required": False, "sort_order": 2},
    ]
    first = service.save_active_template(db, "通用检后随访", "desc", qs, updater_id=7)
    assert first["id"] is not None
    assert len(first["questions"]) == 2

    second = service.save_active_template(db, "通用检后随访", "desc2",
                                          [{"question_type": "text",
                                            "question_text": "只有一题",
                                            "is_required": True}])
    assert second["id"] == first["id"]
    assert len(second["questions"]) == 1
    assert second["questions"][0]["question_text"] == "只有一题"
    # 全量替换:旧题目行已清空
    assert db.query(FollowupTemplateQuestion).count() == 1

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_service_template.py`
Expected: FAIL(`ImportError: cannot import name 'service' from 'app.modules.followup'`)。

- [ ] **Step 3: 实现模板部分**

创建 `backend/app/modules/followup/service.py`:

```python
import json
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.database import get_template_db
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from app.modules.report.models import ReportInfo, ReportIndicator
from app.utils.exceptions import NotFoundException, ValidationException
from .models import (Followup, FollowupQuestion, UserNotification,
                     FollowupTemplate, FollowupTemplateQuestion)

_log = logging.getLogger("app.followup")

QUESTION_TYPES = ("single", "multiple", "text")


# --------------------------------------------------------------------------
# 平台模板(admin-portal,模板库会话)
# --------------------------------------------------------------------------
def get_active_template(db: Session) -> dict:
    tpl = (db.query(FollowupTemplate).filter_by(is_active=1)
           .order_by(FollowupTemplate.id.desc()).first())
    if not tpl:
        return {"id": None, "name": "", "description": None, "questions": []}
    qs = (db.query(FollowupTemplateQuestion)
          .filter(FollowupTemplateQuestion.template_id == tpl.id)
          .order_by(FollowupTemplateQuestion.sort_order,
                    FollowupTemplateQuestion.id).all())
    return {
        "id": tpl.id, "name": tpl.name, "description": tpl.description,
        "questions": [{
            "id": q.id, "question_type": q.question_type,
            "question_text": q.question_text, "options": q.options or [],
            "is_required": bool(q.is_required), "sort_order": q.sort_order,
            "is_active": bool(q.is_active),
        } for q in qs],
    }


def save_active_template(db: Session, name: str, description: Optional[str],
                         questions: List[dict], updater_id: Optional[int] = None) -> dict:
    if not questions:
        raise ValidationException(detail="激活模板不允许空题目")
    for i, q in enumerate(questions, start=1):
        qtype = q.get("question_type")
        qtext = str(q.get("question_text") or "").strip()
        if qtype not in QUESTION_TYPES:
            raise ValidationException(detail=f"第 {i} 题题型不合法")
        if not qtext:
            raise ValidationException(detail=f"第 {i} 题题干不能为空")
        if qtype in ("single", "multiple"):
            opts = q.get("options")
            if not isinstance(opts, list) or not all(isinstance(o, str) and o.strip() for o in opts):
                raise ValidationException(detail=f"第 {i} 题需提供非空选项列表")

    tpl = (db.query(FollowupTemplate).filter_by(is_active=1)
           .order_by(FollowupTemplate.id.desc()).first())
    if not tpl:
        tpl = FollowupTemplate(name=name or "通用检后随访", description=description,
                               is_active=1, updated_by=updater_id)
        db.add(tpl)
        db.flush()
    else:
        tpl.name = name or tpl.name
        tpl.description = description
        tpl.updated_by = updater_id

    # 全量替换:历史题目无引用(实例已快照),删除重建最稳
    db.query(FollowupTemplateQuestion).filter(
        FollowupTemplateQuestion.template_id == tpl.id).delete(synchronize_session=False)
    db.flush()
    for idx, q in enumerate(questions):
        qtype = q["question_type"]
        db.add(FollowupTemplateQuestion(
            template_id=tpl.id, question_type=qtype,
            question_text=str(q.get("question_text") or "").strip(),
            options=(q.get("options") or []) if qtype != "text" else None,
            is_required=1 if q.get("is_required", True) else 0,
            sort_order=int(q.get("sort_order") or idx),
            is_active=0 if q.get("is_active") is False else 1,
        ))
    db.commit()
    return get_active_template(db)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_service_template.py`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/followup/service.py backend/tests/followup/test_service_template.py
git commit -m "feat(followup): 平台激活模板读写 get_active_template / save_active_template"
```

---

### Task 4: worker 钩子生成 —— try_generate_followup

**Files:**
- Modify: `backend/app/modules/followup/service.py`(追加生成逻辑)
- Test: `backend/tests/followup/test_service_generate.py`

**Interfaces:**
- Produces `service.try_generate_followup(db: Session, report_id: int) -> bool`(True=本次新建;False=跳过或失败)。
- Consumes Task 1 DDL、Task 2 模型、Task 3 的 `_log`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/followup/test_service_generate.py`:

```python
"""try_generate_followup:触发 / 跳过 / 幂等 / 快照 / 吞错。"""
from datetime import date, datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from app.models.base import Base
from app.modules.report.models import ReportInfo, ReportIndicator
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from app.modules.followup.models import (  # noqa: F401
    Followup, FollowupQuestion, UserNotification,
    FollowupTemplate, FollowupTemplateQuestion,
)
from app.modules.followup import service


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _tdb_gen(session):
    yield session


def _seed_template(session, questions=None):
    questions = questions or [
        {"question_type": "single", "question_text": "近期是否头晕？",
         "options": ["经常", "偶尔", "无"], "is_required": 1, "sort_order": 1, "is_active": 1},
        {"question_type": "text", "question_text": "补充说明",
         "options": None, "is_required": 0, "sort_order": 2, "is_active": 1},
    ]
    tpl = FollowupTemplate(name="通用检后随访", is_active=1)
    session.add(tpl)
    session.flush()
    for q in questions:
        session.add(FollowupTemplateQuestion(template_id=tpl.id, **q))
    session.commit()
    return tpl


def _seed_red_report(session, level="red"):
    r = ReportInfo(user_id="123456", name="张三", report_date=date(2026, 8, 30))
    session.add(r)
    session.flush()
    session.add(ReportIndicator(report_id=r.id, item_name="甘油三酯",
                                result_value="2.8", unit="mmol/L",
                                ref_range_low="0.4", ref_range_high="1.7"))
    session.add(ReportIndicator(report_id=r.id, item_name="谷丙转氨酶",
                                result_value="60", unit="U/L"))
    session.flush()
    interp = ReportInterpretation(report_id=r.id, overall_level=level, status="completed")
    session.add(interp)
    session.flush()
    session.add(IndicatorJudgment(interpretation_id=interp.id, indicator_id=1,
                                  item_name="甘油三酯", result_value="2.8",
                                  color_level="red", deviation="high"))
    session.add(IndicatorJudgment(interpretation_id=interp.id, indicator_id=2,
                                  item_name="谷丙转氨酶", result_value="60",
                                  color_level="yellow", deviation="high"))
    session.commit()
    return r.id


def _patch_tdb(session):
    return patch("app.modules.followup.service.get_template_db",
                 lambda: _tdb_gen(session))


def test_red_report_creates_followup_and_notification(db):
    rid = _seed_red_report(db, "red")
    _seed_template(db)
    with _patch_tdb(db):
        ok = service.try_generate_followup(db, rid)
    assert ok is True
    fup = db.query(Followup).filter_by(report_id=rid).first()
    assert fup.status == "pending"
    assert fup.user_id == "123456"
    assert fup.overall_level == "red"
    assert fup.template_name == "通用检后随访"
    assert len(fup.recheck_indicators_json) == 2
    by_name = {it["item_name"]: it for it in fup.recheck_indicators_json}
    assert set(by_name) == {"甘油三酯", "谷丙转氨酶"}
    assert by_name["甘油三酯"]["color_level"] == "red"
    assert by_name["甘油三酯"]["ref_range"] == "0.4-1.7"
    qs = (db.query(FollowupQuestion).filter_by(followup_id=fup.id)
          .order_by(FollowupQuestion.sort_order).all())
    assert [q.question_text for q in qs] == ["近期是否头晕？", "补充说明"]
    notif = db.query(UserNotification).filter_by(user_id="123456").first()
    assert notif.category == "recheck_reminder"
    assert notif.ref_followup_id == fup.id
    assert notif.content["overall_level"] == "red"
    assert "2 项异常" in notif.title


def test_green_report_skips(db):
    rid = _seed_red_report(db, "green")
    _seed_template(db)
    with _patch_tdb(db):
        assert service.try_generate_followup(db, rid) is False
    assert db.query(Followup).filter_by(report_id=rid).count() == 0
    assert db.query(UserNotification).count() == 0


def test_duplicate_call_idempotent(db):
    rid = _seed_red_report(db, "yellow")
    _seed_template(db)
    with _patch_tdb(db):
        assert service.try_generate_followup(db, rid) is True
        assert service.try_generate_followup(db, rid) is False
    assert db.query(Followup).filter_by(report_id=rid).count() == 1
    assert db.query(UserNotification).count() == 1


def test_no_active_template_skips(db):
    rid = _seed_red_report(db)
    with _patch_tdb(db):  # 无模板行
        assert service.try_generate_followup(db, rid) is False
    assert db.query(Followup).count() == 0
    assert db.query(UserNotification).count() == 0


def test_inactive_question_not_snapshotted(db):
    rid = _seed_red_report(db)
    _seed_template(db, questions=[
        {"question_type": "text", "question_text": "启用题",
         "options": None, "is_required": 1, "sort_order": 1, "is_active": 1},
        {"question_type": "text", "question_text": "停用题",
         "options": None, "is_required": 1, "sort_order": 2, "is_active": 0},
    ])
    with _patch_tdb(db):
        service.try_generate_followup(db, rid)
    fup = db.query(Followup).filter_by(report_id=rid).first()
    qs = db.query(FollowupQuestion).filter_by(followup_id=fup.id).all()
    assert [q.question_text for q in qs] == ["启用题"]


def test_snapshot_stable_after_template_change(db):
    rid = _seed_red_report(db)
    _seed_template(db)
    with _patch_tdb(db):
        service.try_generate_followup(db, rid)
    fup = db.query(Followup).filter_by(report_id=rid).first()
    qs = db.query(FollowupQuestion).filter_by(followup_id=fup.id).all()
    assert qs[0].question_text == "近期是否头晕？"
    # 改动平台模板 → 实例快照不变
    db.query(FollowupTemplateQuestion).update({FollowupTemplateQuestion.question_text: "改后题干"})
    db.commit()
    assert db.query(FollowupQuestion).filter_by(followup_id=fup.id).first().question_text == "近期是否头晕？"


def test_failure_swallowed_and_rolled_back(db):
    rid = _seed_red_report(db)
    _seed_template(db)
    with _patch_tdb(db):
        with patch.object(service, "_load_active_template",
                          side_effect=RuntimeError("boom")):
            assert service.try_generate_followup(db, rid) is False
    assert db.query(Followup).count() == 0
    assert db.query(UserNotification).count() == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_service_generate.py`
Expected: FAIL(`AttributeError: module 'app.modules.followup.service' has no attribute 'try_generate_followup'`)。

- [ ] **Step 3: 追加生成实现到 service.py**

在 `service.py` 末尾追加(含 `_load_active_template` 与 `_fmt` 助手,供后续任务复用):

```python
# --------------------------------------------------------------------------
# 生成(interpretation worker 钩子,医院库会话;模板库会话函数内自开)
# --------------------------------------------------------------------------
def _fmt(dt):
    return dt.isoformat() if dt else None


def _load_active_template() -> "tuple[Optional[FollowupTemplate], list[FollowupTemplateQuestion]]":
    gen = get_template_db()
    db = next(gen)
    try:
        tpl = (db.query(FollowupTemplate).filter_by(is_active=1)
               .order_by(FollowupTemplate.id.desc()).first())
        if not tpl:
            return None, []
        qs = (db.query(FollowupTemplateQuestion)
              .filter(FollowupTemplateQuestion.template_id == tpl.id,
                      FollowupTemplateQuestion.is_active == 1)
              .order_by(FollowupTemplateQuestion.sort_order,
                        FollowupTemplateQuestion.id).all())
        return tpl, qs
    finally:
        gen.close()


def try_generate_followup(db: Session, report_id: int) -> bool:
    """解读 worker 钩子:红/黄报告 → 建随访问卷(pending)+ 复查提醒。

    绿/无解读/同 report_id 已存在/无激活模板 → False(不报错)。
    任意异常 → rollback + app.followup 日志,不上抛(与 comparison summary 同款容错)。
    """
    try:
        return _do_generate_followup(db, report_id)
    except Exception:
        db.rollback()
        _log.exception("followup generate failed report_id=%s", report_id)
        return False


def _do_generate_followup(db: Session, report_id: int) -> bool:
    report = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not report:
        return False
    interp = (db.query(ReportInterpretation)
              .filter(ReportInterpretation.report_id == report_id)
              .order_by(ReportInterpretation.id.desc()).first())
    if not interp or interp.status != "completed" or interp.overall_level not in ("red", "yellow"):
        return False
    if db.query(Followup).filter(Followup.report_id == report_id).first():
        return False
    tpl, questions = _load_active_template()
    if not tpl:
        _log.warning("followup skip: no active template report_id=%s", report_id)
        return False

    rows = (db.query(IndicatorJudgment, ReportIndicator)
            .join(ReportIndicator, IndicatorJudgment.indicator_id == ReportIndicator.id)
            .filter(IndicatorJudgment.interpretation_id == interp.id,
                    IndicatorJudgment.color_level.in_(["red", "yellow"]))
            .all())
    recheck = []
    for j, ind in rows:
        lo, hi = ind.ref_range_low, ind.ref_range_high
        recheck.append({
            "item_name": j.item_name, "result_value": j.result_value,
            "unit": ind.unit,
            "ref_range": f"{lo}-{hi}" if lo and hi else None,
            "color_level": j.color_level,
        })

    report_date = report.report_date.isoformat() if report.report_date else None
    fup = Followup(report_id=report_id, user_id=report.user_id, name=report.name,
                   overall_level=interp.overall_level, status="pending",
                   recheck_indicators_json=recheck, template_name=tpl.name)
    db.add(fup)
    db.flush()
    for q in questions:
        db.add(FollowupQuestion(followup_id=fup.id, question_type=q.question_type,
                                question_text=q.question_text, options=q.options,
                                is_required=q.is_required, sort_order=q.sort_order))
    n = len(recheck)
    db.add(UserNotification(
        user_id=report.user_id, name=report.name, category="recheck_reminder",
        title=f"您 {report_date or '最近'} 的体检存在 {n} 项异常,请及时关注并按需复查",
        content={"report_id": report_id, "report_date": report_date,
                 "overall_level": interp.overall_level, "followup_pending": True,
                 "recheck_indicators": recheck},
        ref_report_id=report_id, ref_followup_id=fup.id, is_read=0))
    db.commit()
    return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_service_generate.py`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/followup/service.py backend/tests/followup/test_service_generate.py
git commit -m "feat(followup): 解读 worker 钩子 try_generate_followup(红黄触发/快照/幂等/吞错)"
```

---

### Task 5: 用户/医生/删除相关 service(list/detail/submit/notifications/by-report/delete)

**Files:**
- Modify: `backend/app/modules/followup/service.py`(追加查询与提交)
- Test: `backend/tests/followup/test_service_api.py`、`backend/tests/followup/test_service_delete.py`

**Interfaces:**
- Produces:
  - `service.list_my_followups(db, uid, nm, page, page_size) -> dict`
  - `service.get_followup_detail(db, uid, nm, followup_id) -> Optional[dict]`
  - `service.submit_followup(db, uid, nm, followup_id, answers) -> dict`
  - `service.list_my_notifications(db, uid, nm, page, page_size, unread_only) -> dict`
  - `service.count_unread(db, uid, nm) -> int`
  - `service.mark_notification_read(db, uid, nm, nid) -> bool`
  - `service.mark_all_notifications_read(db, uid, nm) -> int`
  - `service.get_followup_by_report(db, report_id) -> Optional[dict]`
  - `service.delete_report_followup(db, report_id) -> None`
- Consumes Task 2 模型、Task 4 的 `_fmt`;抛 `NotFoundException`/`ValidationException`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/followup/test_service_api.py`:

```python
"""用户侧查询/提交 + 通知已读 + 医生 by-report。"""
from datetime import date, datetime
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportInfo
from app.modules.interpretation.models import ReportInterpretation
from app.modules.followup.models import (  # noqa: F401
    Followup, FollowupQuestion, UserNotification,
    FollowupTemplate, FollowupTemplateQuestion,
)
from app.modules.followup import service
from app.utils.exceptions import NotFoundException, ValidationException


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _mk_followup(db, fid=1, uid="123456", nm="张三", status="pending"):
    db.add(ReportInfo(id=fid, user_id=uid, name=nm))
    db.add(ReportInterpretation(report_id=fid, status="completed", overall_level="yellow"))
    f = Followup(id=fid, report_id=fid, user_id=uid, name=nm,
                 overall_level="yellow", status=status,
                 recheck_indicators_json=[{"item_name": "血压", "color_level": "yellow"}],
                 template_name="通用检后随访")
    db.add(f)
    db.flush()
    db.add(FollowupQuestion(followup_id=fid, question_type="single",
                            question_text="近期是否头晕？", options=["是", "否"],
                            is_required=1, sort_order=1))
    db.add(FollowupQuestion(followup_id=fid, question_type="multiple",
                            question_text="有哪些症状？", options=["头痛", "心悸"],
                            is_required=0, sort_order=2))
    db.add(FollowupQuestion(followup_id=fid, question_type="text",
                            question_text="补充", options=None,
                            is_required=0, sort_order=3))
    db.commit()
    return f


def test_list_only_own(db):
    _mk_followup(db, fid=1, uid="123456", nm="张三")
    _mk_followup(db, fid=2, uid="999999", nm="李四")
    r = service.list_my_followups(db, "123456", "张三", 1, 20)
    assert r["total"] == 1
    assert r["items"][0]["report_id"] == 1
    assert r["has_pending"] is True


def test_get_detail_ownership(db):
    _mk_followup(db, fid=1)
    assert service.get_followup_detail(db, "123456", "张三", 1) is not None
    assert service.get_followup_detail(db, "999999", "李四", 1) is None


def test_submit_requires_required(db):
    _mk_followup(db, fid=1)
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1, [{"question_id": 2, "answer": ["头痛"]}])


def test_submit_rejects_unknown_question(db):
    _mk_followup(db, fid=1)
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1, [{"question_id": 99, "answer": "x"}])


def test_submit_rejects_invalid_option(db):
    _mk_followup(db, fid=1)
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1,
                                [{"question_id": 1, "answer": "不存在"}])
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1,
                                [{"question_id": 2, "answer": ["不存在"]}])


def test_submit_duplicate_question_id(db):
    _mk_followup(db, fid=1)
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1,
                                [{"question_id": 1, "answer": "是"},
                                 {"question_id": 1, "answer": "否"}])


def test_submit_success_marks_completed(db):
    _mk_followup(db, fid=1)
    service.submit_followup(db, "123456", "张三", 1, [
        {"question_id": 1, "answer": "是"},
        {"question_id": 2, "answer": ["头痛", "心悸"]},
        {"question_id": 3, "answer": "暂无"},
    ])
    f = db.query(Followup).filter_by(id=1).first()
    assert f.status == "completed"
    assert f.submitted_at is not None
    qs = {q.id: q for q in db.query(FollowupQuestion).filter_by(followup_id=1).all()}
    assert qs[1].answer == "是"
    assert json.loads(qs[2].answer) == ["头痛", "心悸"]
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1, [])


def test_notifications_read_flow(db):
    _mk_followup(db, fid=1)
    n1 = UserNotification(user_id="123456", name="张三", category="recheck_reminder",
                          title="t1", content={"a": 1}, ref_report_id=1, ref_followup_id=1, is_read=0)
    n2 = UserNotification(user_id="123456", name="张三", category="recheck_reminder",
                          title="t2", content={"a": 2}, ref_report_id=1, ref_followup_id=1, is_read=0)
    db.add_all([n1, n2]); db.commit()
    assert service.count_unread(db, "123456", "张三") == 2
    assert service.count_unread(db, "999999", "李四") == 0
    r = service.list_my_notifications(db, "123456", "张三", 1, 20)
    assert r["total"] == 2
    assert service.mark_notification_read(db, "123456", "张三", n1.id) is True
    assert service.count_unread(db, "123456", "张三") == 1
    assert service.mark_notification_read(db, "999999", "李四", n1.id) is False
    assert service.mark_all_notifications_read(db, "123456", "张三") == 1


def test_doctor_by_report(db):
    _mk_followup(db, fid=1)
    d = service.get_followup_by_report(db, 1)
    assert d["status"] == "pending"
    assert len(d["questions"]) == 3
    assert service.get_followup_by_report(db, 999) is None
```

创建 `backend/tests/followup/test_service_delete.py`:

```python
"""删除报告联动:清随访/答卷/通知,不动他人数据。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportInfo
from app.modules.interpretation.models import ReportInterpretation
from app.modules.followup.models import (  # noqa: F401
    Followup, FollowupQuestion, UserNotification,
    FollowupTemplate, FollowupTemplateQuestion,
)
from app.modules.followup import service


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _seed(db, report_id=1, uid="123456"):
    db.add(ReportInfo(id=report_id, user_id=uid, name="张三"))
    f = Followup(report_id=report_id, user_id=uid, name="张三", overall_level="red")
    db.add(f); db.flush()
    db.add(FollowupQuestion(followup_id=f.id, question_type="text",
                            question_text="q", is_required=1, sort_order=1))
    db.add(UserNotification(user_id=uid, name="张三", category="recheck_reminder",
                            title="t", content={"a": 1},
                            ref_report_id=report_id, ref_followup_id=f.id))
    db.commit()
    return f


def test_delete_report_followup_cleans(db):
    _seed(db, report_id=1)
    _seed(db, report_id=2)  # 无关报告,须保留
    service.delete_report_followup(db, 1)
    assert db.query(Followup).filter_by(report_id=1).count() == 0
    assert db.query(FollowupQuestion).filter_by(followup_id=1).count() == 0
    assert db.query(UserNotification).filter(UserNotification.ref_report_id == 1).count() == 0
    assert db.query(Followup).filter_by(report_id=2).count() == 1
    assert db.query(UserNotification).filter(UserNotification.ref_report_id == 2).count() == 1
```

- [ ] **Step 2: 运行确认失败**

Run:
```
cd backend && .venv/bin/python -m pytest -q tests/followup/test_service_api.py tests/followup/test_service_delete.py
```
Expected: FAIL(`AttributeError: module ... has no attribute 'list_my_followups'`)。

- [ ] **Step 3: 追加实现到 service.py**

在 `service.py` 末尾追加:

```python
# --------------------------------------------------------------------------
# 用户侧查询/提交(role='user',双锚定 uid/nm 由路由传入)
# --------------------------------------------------------------------------
def _answer_out(q) -> object:
    if q.question_type == "multiple" and q.answer:
        try:
            return json.loads(q.answer)
        except Exception:
            return q.answer
    return q.answer


def list_my_followups(db: Session, uid: str, nm: Optional[str],
                      page: int = 1, page_size: int = 20) -> dict:
    q = db.query(Followup).filter(Followup.user_id == uid, Followup.name == nm)
    total = q.count()
    rows = q.order_by(Followup.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [{
        "id": f.id, "report_id": f.report_id, "status": f.status,
        "overall_level": f.overall_level,
        "recheck_indicators": f.recheck_indicators_json or [],
        "template_name": f.template_name,
        "generated_at": _fmt(f.generated_at), "submitted_at": _fmt(f.submitted_at),
    } for f in rows]
    has_pending = (db.query(Followup.id)
                   .filter(Followup.user_id == uid, Followup.name == nm,
                           Followup.status == "pending").first() is not None)
    return {"items": items, "total": total, "page": page,
            "page_size": page_size, "has_pending": has_pending}


def get_followup_detail(db: Session, uid: str, nm: Optional[str], followup_id: int):
    f = (db.query(Followup).filter(Followup.id == followup_id,
                                   Followup.user_id == uid, Followup.name == nm).first())
    if not f:
        return None
    qs = (db.query(FollowupQuestion).filter(FollowupQuestion.followup_id == f.id)
          .order_by(FollowupQuestion.sort_order, FollowupQuestion.id).all())
    return {
        "id": f.id, "report_id": f.report_id, "status": f.status,
        "overall_level": f.overall_level, "template_name": f.template_name,
        "recheck_indicators": f.recheck_indicators_json or [],
        "generated_at": _fmt(f.generated_at), "submitted_at": _fmt(f.submitted_at),
        "questions": [{
            "id": q.id, "question_type": q.question_type,
            "question_text": q.question_text, "options": q.options or [],
            "is_required": bool(q.is_required), "sort_order": q.sort_order,
            "answer": _answer_out(q),
        } for q in qs],
    }


def submit_followup(db: Session, uid: str, nm: Optional[str],
                    followup_id: int, answers: list) -> dict:
    f = (db.query(Followup).filter(Followup.id == followup_id,
                                   Followup.user_id == uid, Followup.name == nm).first())
    if not f:
        raise NotFoundException(detail="随访问卷不存在")
    if f.status == "completed":
        raise ValidationException(detail="问卷已提交,不能重复提交")
    if not isinstance(answers, list):
        raise ValidationException(detail="answers 必须是数组")
    qs = (db.query(FollowupQuestion).filter(FollowupQuestion.followup_id == f.id)
          .order_by(FollowupQuestion.sort_order, FollowupQuestion.id).all())
    if not qs:
        raise ValidationException(detail="问卷无题目")
    by_qid: dict = {}
    for it in answers:
        if not isinstance(it, dict) or it.get("question_id") is None:
            raise ValidationException(detail="answers 每项需含 question_id")
        qid = int(it["question_id"])
        if qid in by_qid:
            raise ValidationException(detail=f"题目 {qid} 重复提交")
        by_qid[qid] = it.get("answer")
    now = datetime.utcnow()
    for q in qs:
        ans = by_qid.get(q.id)
        if isinstance(ans, str):
            ans = ans.strip()
        if q.is_required and ans in (None, ""):
            raise ValidationException(detail=f"题目「{q.question_text}」为必填")
        if q.question_type in ("single", "multiple"):
            allowed = list(q.options or [])
            if ans in (None, ""):
                ans = None
            elif q.question_type == "single":
                if not isinstance(ans, str) or ans not in allowed:
                    raise ValidationException(detail=f"题目「{q.question_text}」选项不合法")
            else:
                lst = ans if isinstance(ans, list) else [ans]
                if not all(x in allowed for x in lst):
                    raise ValidationException(detail=f"题目「{q.question_text}」选项不合法")
                ans = lst
        elif q.question_type == "text":
            if ans is not None and not isinstance(ans, str):
                raise ValidationException(detail=f"题目「{q.question_text}」需文本回答")
            if ans is not None and len(ans) > 2000:
                raise ValidationException(detail=f"题目「{q.question_text}」回答过长")
        q.answer = json.dumps(ans, ensure_ascii=False) if isinstance(ans, list) else ans
        q.answered_at = now
    f.status = "completed"
    f.submitted_at = now
    db.commit()
    return get_followup_detail(db, uid, nm, followup_id)


# --------------------------------------------------------------------------
# 通知(role='user')
# --------------------------------------------------------------------------
def list_my_notifications(db: Session, uid: str, nm: Optional[str],
                          page: int = 1, page_size: int = 20,
                          unread_only: bool = False) -> dict:
    q = db.query(UserNotification).filter(UserNotification.user_id == uid,
                                          UserNotification.name == nm)
    if unread_only:
        q = q.filter(UserNotification.is_read == 0)
    total = q.count()
    rows = q.order_by(UserNotification.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [{
        "id": n.id, "category": n.category, "title": n.title,
        "content": n.content, "is_read": bool(n.is_read),
        "ref_report_id": n.ref_report_id, "ref_followup_id": n.ref_followup_id,
        "created_at": _fmt(n.created_at),
    } for n in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def count_unread(db: Session, uid: str, nm: Optional[str]) -> int:
    return (db.query(UserNotification.id)
            .filter(UserNotification.user_id == uid, UserNotification.name == nm,
                    UserNotification.is_read == 0).count())


def mark_notification_read(db: Session, uid: str, nm: Optional[str], nid: int) -> bool:
    n = (db.query(UserNotification).filter(UserNotification.id == nid,
                                          UserNotification.user_id == uid,
                                          UserNotification.name == nm).first())
    if not n:
        return False
    n.is_read = 1
    n.read_at = datetime.utcnow()
    db.commit()
    return True


def mark_all_notifications_read(db: Session, uid: str, nm: Optional[str]) -> int:
    now = datetime.utcnow()
    n = (db.query(UserNotification)
         .filter(UserNotification.user_id == uid, UserNotification.name == nm,
                 UserNotification.is_read == 0)
         .update({UserNotification.is_read: 1, UserNotification.read_at: now},
                 synchronize_session=False))
    db.commit()
    return n


# --------------------------------------------------------------------------
# 医生按报告查看(doctor/admin,医院库内 report 维度)
# --------------------------------------------------------------------------
def get_followup_by_report(db: Session, report_id: int):
    f = db.query(Followup).filter(Followup.report_id == report_id).first()
    if not f:
        return None
    qs = (db.query(FollowupQuestion).filter(FollowupQuestion.followup_id == f.id)
          .order_by(FollowupQuestion.sort_order, FollowupQuestion.id).all())
    return {
        "id": f.id, "report_id": f.report_id, "user_id": f.user_id, "name": f.name,
        "status": f.status, "overall_level": f.overall_level,
        "template_name": f.template_name,
        "recheck_indicators": f.recheck_indicators_json or [],
        "generated_at": _fmt(f.generated_at), "submitted_at": _fmt(f.submitted_at),
        "questions": [{
            "id": q.id, "question_type": q.question_type,
            "question_text": q.question_text, "options": q.options or [],
            "is_required": bool(q.is_required), "sort_order": q.sort_order,
            "answer": _answer_out(q),
        } for q in qs],
    }


# --------------------------------------------------------------------------
# 删除报告联动(report/router.py delete_report 调用)
# --------------------------------------------------------------------------
def delete_report_followup(db: Session, report_id: int) -> None:
    ids = [r[0] for r in db.query(Followup.id).filter(Followup.report_id == report_id).all()]
    if ids:
        db.query(FollowupQuestion).filter(
            FollowupQuestion.followup_id.in_(ids)).delete(synchronize_session=False)
        db.query(Followup).filter(Followup.id.in_(ids)).delete(synchronize_session=False)
    db.query(UserNotification).filter(
        UserNotification.ref_report_id == report_id).delete(synchronize_session=False)
```

- [ ] **Step 4: 运行测试确认通过**

Run:
```
cd backend && .venv/bin/python -m pytest -q tests/followup/test_service_api.py tests/followup/test_service_delete.py
```
Expected: 10 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/followup/service.py backend/tests/followup/test_service_api.py backend/tests/followup/test_service_delete.py
git commit -m "feat(followup): 用户查询/提交 + 通知已读 + 医生 by-report + 删除联动 service"
```

---

### Task 6: router + main.py 装配 + 权限/路由测试

**Files:**
- Create: `backend/app/modules/followup/router.py`
- Modify: `backend/app/main.py`(import + include_router ×2)
- Test: `backend/tests/followup/test_router.py`

**Interfaces:**
- Produces 两个 APIRouter:`followup_router`(挂 `/api/v1/followup`)、`notification_router`(挂 `/api/v1/notifications`)。
- Consumes Task 2 schemas、Task 3-5 service、`app.core.dependencies`、`app.utils.exceptions`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/followup/test_router.py`:

```python
"""路由注册 + 角色/无后缀语义(service 已单测,这里只测路由层)。"""
from fastapi.testclient import TestClient
from unittest.mock import patch
import app.main as main_mod
from app.core.dependencies import get_current_user, CurrentUser


def _override(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def test_routes_registered():
    paths = {getattr(r, "path", None) for r in main_mod.app.routes}
    assert "/api/v1/followup/center" in paths
    assert "/api/v1/followup/template" in paths
    assert "/api/v1/followup/{followup_id}/submit" in paths
    assert "/api/v1/notifications" in paths
    assert "/api/v1/notifications/unread-count" in paths


def test_user_no_suffix_center_returns_empty():
    app = _override(main_mod.app, CurrentUser(user_id=5, role="user",
                                              hospital_id="H001",
                                              id_card_suffix=None, name=None))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/followup/center")
        assert r.status_code == 200
        assert r.json() == {"items": [], "total": 0, "page": 1,
                            "page_size": 20, "has_pending": False}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_user_no_suffix_unread_zero():
    app = _override(main_mod.app, CurrentUser(user_id=5, role="user",
                                              hospital_id="H001",
                                              id_card_suffix=None, name=None))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/notifications/unread-count")
        assert r.status_code == 200
        assert r.json() == {"unread_count": 0}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_admin_template_read_non_admin_403():
    app = _override(main_mod.app, CurrentUser(user_id=1, role="doctor", hospital_id="H001"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/followup/template")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_admin_template_write_empty_400():
    app = _override(main_mod.app, CurrentUser(user_id=1, role="admin", hospital_id=None))
    try:
        with TestClient(app) as c:
            # 空题目:真实 service.save_active_template 在校验阶段抛 ValidationException → 400
            # (未触碰模板库连接;get_template_db 依赖惰性建会话)
            r = c.put("/api/v1/followup/template",
                      json={"name": "通用", "questions": []})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_by_report_user_forbidden():
    app = _override(main_mod.app, CurrentUser(user_id=5, role="user",
                                              hospital_id="H001",
                                              id_card_suffix="123456", name="张三"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/followup/by-report/1")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_doctor_by_report_404_when_none(monkeypatch):
    app = _override(main_mod.app, CurrentUser(user_id=2, role="doctor", hospital_id="H001"))
    try:
        with TestClient(app) as c:
            with patch("app.modules.followup.router.service.get_followup_by_report",
                       return_value=None):
                r = c.get("/api/v1/followup/by-report/999")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_router.py`
Expected: FAIL(`ModuleNotFoundError: app.modules.followup.router`)。

- [ ] **Step 3: 实现 router.py**

创建 `backend/app/modules/followup/router.py`:

```python
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db, get_template_db
from app.core.dependencies import (get_current_user, CurrentUser,
                                   user_identity, require_role)
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.followup import service, schemas

followup_router = APIRouter()
notification_router = APIRouter()


def _get_db(current_user: CurrentUser = Depends(get_current_user)):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


def _user_anchor(current_user) -> tuple[Optional[str], Optional[str]]:
    uid, nm = user_identity(current_user)
    return uid, nm


# --------------------------------------------------------------------------
# 平台管理员:激活模板读 / 写(模板库,role=admin)
# --------------------------------------------------------------------------
@followup_router.get("/template")
def get_template(db: Session = Depends(get_template_db),
                 current_user: CurrentUser = Depends(require_role("admin"))):
    return service.get_active_template(db)


@followup_router.put("/template")
def put_template(payload: schemas.TemplateSaveRequest,
                 db: Session = Depends(get_template_db),
                 current_user: CurrentUser = Depends(require_role("admin"))):
    questions = [q.model_dump() for q in payload.questions]
    return service.save_active_template(
        db, payload.name, payload.description, questions,
        updater_id=current_user.user_id)


# --------------------------------------------------------------------------
# 用户侧(role=user,双锚定;app-login JWT 直接可用)
# --------------------------------------------------------------------------
@followup_router.get("/center")
def my_center(page: int = Query(1, ge=1),
              page_size: int = Query(20, ge=1, le=100),
              db: Session = Depends(_get_db),
              current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        return {"items": [], "total": 0, "page": page,
                "page_size": page_size, "has_pending": False}
    return service.list_my_followups(db, uid, nm, page, page_size)


@followup_router.post("/{followup_id}/submit")
def submit(followup_id: int, payload: schemas.SubmitRequest,
           db: Session = Depends(_get_db),
           current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        raise NotFoundException(detail="随访问卷不存在")
    answers = [{"question_id": a.question_id, "answer": a.answer}
               for a in payload.answers]
    return service.submit_followup(db, uid, nm, followup_id, answers)


@followup_router.get("/{followup_id}")
def detail(followup_id: int,
           db: Session = Depends(_get_db),
           current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        raise NotFoundException(detail="随访问卷不存在")
    data = service.get_followup_detail(db, uid, nm, followup_id)
    if not data:
        raise NotFoundException(detail="随访问卷不存在")
    return data


# --------------------------------------------------------------------------
# 医生侧:按报告查看(doctor/admin)
# --------------------------------------------------------------------------
@followup_router.get("/by-report/{report_id}")
def by_report(report_id: int, db: Session = Depends(_get_db),
              current_user: CurrentUser = Depends(require_role("doctor", "admin"))):
    data = service.get_followup_by_report(db, report_id)
    if not data:
        raise NotFoundException(detail="该报告没有随访记录")
    return data


# --------------------------------------------------------------------------
# 通知(role=user)
# --------------------------------------------------------------------------
@notification_router.get("")
def notifications(page: int = Query(1, ge=1),
                  page_size: int = Query(20, ge=1, le=100),
                  unread_only: bool = Query(False),
                  db: Session = Depends(_get_db),
                  current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    return service.list_my_notifications(db, uid, nm, page, page_size, unread_only)


@notification_router.get("/unread-count")
def unread_count(db: Session = Depends(_get_db),
                 current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        return {"unread_count": 0}
    return {"unread_count": service.count_unread(db, uid, nm)}


@notification_router.post("/{notification_id}/read")
def mark_read(notification_id: int, db: Session = Depends(_get_db),
              current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None or not service.mark_notification_read(db, uid, nm, notification_id):
        raise NotFoundException(detail="通知不存在")
    return {"status": "ok"}


@notification_router.post("/read-all")
def mark_all_read(db: Session = Depends(_get_db),
                  current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        return {"status": "ok"}
    service.mark_all_notifications_read(db, uid, nm)
    return {"status": "ok"}
```

> **重要**:路由声明顺序里 `/{followup_id}` 等动态段最后;而 `/template`、`/center`、`/by-report/{report_id}` 都排在动态段之前(见上面定义顺序)。`POST /{followup_id}/submit` 是 `/{followup_id}` 的子路径,无冲突;`/notifications` 的空路径路由用 `@notification_router.get("")`(main 里 prefix 为 `/api/v1/notifications`),保证 `GET /api/v1/notifications` 命中。

- [ ] **Step 4: main.py 装配**

修改 `backend/app/main.py`:import 区(第 22 行 `tenant_router` 之后)加:

```python
from app.modules.followup.router import followup_router, notification_router
```

并在 `app.include_router(tenant_router, ...)`(第 52 行)之后加:

```python
    app.include_router(followup_router, prefix="/api/v1/followup", tags=["followup"])
    app.include_router(notification_router, prefix="/api/v1/notifications", tags=["notifications"])
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_router.py tests/test_main_wiring.py`
Expected: 7 passed(+ main wiring 通过)。

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/followup/router.py backend/app/main.py backend/tests/followup/test_router.py
git commit -m "feat(followup): followup/notifications router + main 装配 + 路由权限测试"
```

---

### Task 7: 解读 worker 钩子接线 + 报告删除联动接线

**Files:**
- Modify: `backend/app/modules/interpretation/worker.py:56-66`(comparison summary try 块后追加)
- Modify: `backend/app/modules/report/router.py:153-168`(delete_report 内追加)
- Test: `backend/tests/followup/test_worker_hook.py`

**Interfaces:**
- Consumes Task 4 `try_generate_followup`、Task 5 `delete_report_followup`。

- [ ] **Step 1: 写失败测试(钩子被调用 + 失败不影响解读)**

创建 `backend/tests/followup/test_worker_hook.py`(沿用 `tests/test_interp_worker_bulk.py` 的 sqlite + patch 手法):

```python
"""interpretation worker 成功分支应调用 try_generate_followup;其失败不影响解读完成。"""
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportInfo  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation  # noqa: F401
from app.modules.followup.models import (  # noqa: F401
    Followup, FollowupQuestion, UserNotification,
    FollowupTemplate, FollowupTemplateQuestion,
)


def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


def _gen(session):
    yield session


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_int(ReportInfo.__table__.c.id,
                      ReportInterpretation.__table__.c.id,
                      ReportInterpretation.__table__.c.report_id)
    Base.metadata.create_all(engine)
    _restore(saved)
    s = sessionmaker(bind=engine)()
    Mq = MagicMock()
    p_getdb = patch("app.modules.interpretation.worker.get_hospital_db", lambda hid: _gen(s))
    p_win = patch("app.modules.interpretation.worker.is_bulk_window_now", return_value=True)
    p_agent = patch("app.modules.interpretation.worker.run_interpretation_agent")
    p_cmp = patch("app.modules.user_profile.service.try_generate_comparison_summary")
    p_batch = patch("app.modules.interpretation.worker.BatchService")
    p_rabbit = patch("app.modules.interpretation.worker.rabbitmq", Mq)
    p_followup = patch("app.modules.followup.service.try_generate_followup")
    for p in (p_getdb, p_win, p_agent, p_cmp, p_batch, p_rabbit, p_followup):
        p.start()
    try:
        yield s, p_agent, p_followup
    finally:
        for p in (p_getdb, p_win, p_agent, p_cmp, p_batch, p_rabbit, p_followup):
            p.stop()
        s.close()


def _make_report(db):
    r = ReportInfo(user_id="123456", name="张三")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_success_calls_followup_hook(env):
    s, agent_mock, followup_mock = env
    r = _make_report(s)
    agent_mock.return_value = {}
    from app.modules.interpretation.worker import handle_interpretation_task
    msg = {"_routing_key": "interpretation.normal",
           "payload": {"report_id": r.id, "hospital_id": "H001"}}
    handle_interpretation_task(msg)
    followup_mock.assert_called_once()
    args, _ = followup_mock.call_args
    assert args[1] == r.id


def test_followup_failure_does_not_break(env):
    s, agent_mock, followup_mock = env
    r = _make_report(s)
    agent_mock.return_value = {}
    followup_mock.side_effect = RuntimeError("boom")
    from app.modules.interpretation.worker import handle_interpretation_task
    msg = {"_routing_key": "interpretation.normal",
           "payload": {"report_id": r.id, "hospital_id": "H001"}}
    handle_interpretation_task(msg)  # 不抛
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest -q tests/followup/test_worker_hook.py`
Expected: FAIL(`assert` 计数为 0——尚未接线)。

- [ ] **Step 3: worker.py 接线**

在 `backend/app/modules/interpretation/worker.py` 第 62-66 行 comparison summary 的 except 结束后(即第 66 行后、`# 成功 → 计 batch file 进度` 注释前)插入:

```python
            # register followup questionnaire + recheck reminder(failures don't affect interp completion)
            try:
                from app.modules.followup.service import try_generate_followup
                try_generate_followup(db, report_id)
            except Exception as e:
                print(f"Followup generation failed for report {report_id}: {e}", flush=True)
```

- [ ] **Step 4: report/router.py 删除联动**

在 `backend/app/modules/report/router.py` `delete_report` 中,第 163 行 `chat_session` 删除之后、`if report.task_id:` 之前插入:

```python
    from app.modules.followup.service import delete_report_followup
    delete_report_followup(db, report_id)
```

- [ ] **Step 5: 运行测试确认通过**

Run:
```
cd backend && .venv/bin/python -m pytest -q tests/followup/test_worker_hook.py tests/test_main_wiring.py
```
Expected: 2 passed(+ wiring)。

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/interpretation/worker.py backend/app/modules/report/router.py backend/tests/followup/test_worker_hook.py
git commit -m "feat(followup): 解读 worker 成功钩子 + 报告删除联动清理"
```

---

### Task 8: user-portal —— 随访 store + 第 4 个 tab(含未读 Badge)

**Files:**
- Create: `frontend/packages/user-portal/src/stores/followupStore.ts`
- Modify: `frontend/packages/user-portal/src/components/Layout.tsx`

**Interfaces:**
- Produces `useFollowupStore`(count + refresh),`Layout` 新增 `随访` tab(带 Badge)。后续 Task 9 页面使用。

- [ ] **Step 1: 写 store**

创建 `frontend/packages/user-portal/src/stores/followupStore.ts`:

```typescript
import { create } from 'zustand';
import { useUserStore } from './userStore';

interface FollowupState {
  count: number;
  refresh: () => Promise<void>;
}

export const useFollowupStore = create<FollowupState>((set) => ({
  count: 0,
  refresh: async () => {
    const api = useUserStore.getState().api;
    if (!useUserStore.getState().token) {
      set({ count: 0 });
      return;
    }
    try {
      const r = await api.get('/notifications/unread-count');
      set({ count: r.data?.unread_count ?? 0 });
    } catch {
      // 401 由拦截器处理;其余网络错误保持原计数
    }
  },
}));
```

- [ ] **Step 2: 改 Layout 加 tab 与 Badge**

修改 `frontend/packages/user-portal/src/components/Layout.tsx`:

第 1-3 行 import 改为:

```tsx
import { ReactNode, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Badge } from 'antd';
import { HomeOutlined, MessageOutlined, UserOutlined, NotificationOutlined } from '@ant-design/icons';
import { useFollowupStore } from '../stores/followupStore';
```

tabs 定义改为:

```tsx
const tabs = [
  { key: '/', label: '首页', icon: <HomeOutlined /> },
  { key: '/chat', label: 'AI咨询', icon: <MessageOutlined /> },
  { key: '/followup', label: '随访', icon: <NotificationOutlined />, badge: true },
  { key: '/profile', label: '我的', icon: <UserOutlined /> },
];
```

组件内加轮询与 Badge(在 `const loc = useLocation();` 后):

```tsx
  const count = useFollowupStore(s => s.count);
  const refresh = useFollowupStore(s => s.refresh);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
```

渲染区图标那行(`<span style={{ fontSize: 20 }}>{t.icon}</span>`)改为:

```tsx
            <span style={{ fontSize: 20 }}>
              {t.badge && count > 0 ? <Badge count={count} size="small">{t.icon}</Badge> : t.icon}
            </span>
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && pnpm exec tsc -p packages/user-portal/tsconfig.json --noEmit`
Expected: 无输出(通过)。

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/user-portal/src/stores/followupStore.ts frontend/packages/user-portal/src/components/Layout.tsx
git commit -m "feat(user-portal): 随访 tab + 未读 Badge(followupStore 轮询 unread-count)"
```

---

### Task 9: user-portal —— 随访中心页 + 问卷填写页 + 路由

**Files:**
- Create: `frontend/packages/user-portal/src/pages/FollowUpCenterPage.tsx`
- Create: `frontend/packages/user-portal/src/pages/FollowUpQuestionnairePage.tsx`
- Modify: `frontend/packages/user-portal/src/router.tsx`

**Interfaces:**
- Consumes `useUserStore`(api)、`useFollowupStore`(refresh)、`Layout`。
- Produces 路由 `/followup`、`/followup/:id`。

- [ ] **Step 1: 随访中心页**

创建 `frontend/packages/user-portal/src/pages/FollowUpCenterPage.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Spin, Tabs, Button, Empty, Tag, message } from 'antd';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import { useUserStore } from '../stores/userStore';
import { useFollowupStore } from '../stores/followupStore';

interface RecheckItem {
  item_name: string; result_value: string | null; unit: string | null;
  ref_range: string | null; color_level: string;
}

interface FollowupItem {
  id: number; report_id: number; status: 'pending' | 'completed';
  overall_level: string; recheck_indicators: RecheckItem[];
  template_name: string | null; generated_at: string | null; submitted_at: string | null;
}

interface NotifItem {
  id: number; title: string; category: string; is_read: boolean; created_at: string | null;
}

export default function FollowUpCenterPage() {
  const { api } = useUserStore();
  const nav = useNavigate();
  const refreshBadge = useFollowupStore(s => s.refresh);
  const [loading, setLoading] = useState(true);
  const [followups, setFollowups] = useState<FollowupItem[]>([]);
  const [notifs, setNotifs] = useState<NotifItem[]>([]);

  const load = async () => {
    try {
      const [f, n] = await Promise.all([
        api.get('/followup/center', { params: { page: 1, page_size: 50 } }),
        api.get('/notifications', { params: { page: 1, page_size: 50 } }),
      ]);
      setFollowups(f.data.items || []);
      setNotifs(n.data.items || []);
    } catch { /* 空态处理 */ }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); refreshBadge(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const readAll = async () => {
    await api.post('/notifications/read-all');
    setNotifs(notifs.map(x => ({ ...x, is_read: true })));
    refreshBadge();
    message.success('已全部标记为已读');
  };

  if (loading) return <Layout title="随访"><div style={{ textAlign: 'center', padding: 60 }}><Spin /></div></Layout>;

  const pending = followups.filter(f => f.status === 'pending');
  const done = followups.filter(f => f.status === 'completed');

  const renderFollowup = (f: FollowupItem) => (
    <div key={f.id} style={{
      background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
      padding: '14px 16px', boxShadow: 'var(--shadow-sm)',
      border: '1px solid var(--color-border-light)', marginBottom: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>体检随访</span>
        <ColorBadge level={f.overall_level} size="sm" />
        {f.status === 'completed'
          ? <Tag color="green">已填写</Tag>
          : <Tag color="orange">待填写</Tag>}
        {f.generated_at && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--color-text-secondary)' }}>{f.generated_at.slice(0, 10)}</span>}
      </div>
      {(f.recheck_indicators || []).length > 0 && (
        <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 10 }}>
          {(f.recheck_indicators || []).map((it, i) => (
            <div key={i}>
              {it.item_name}: {it.result_value ?? '-'}{it.unit ? ' ' + it.unit : ''}
              {it.ref_range ? `（参考 ${it.ref_range}）` : ''}
            </div>
          ))}
        </div>
      )}
      {f.status === 'pending' && (
        <Button type="primary" size="small" onClick={() => nav(`/followup/${f.id}`)}>去填写</Button>
      )}
    </div>
  );

  return (
    <Layout title="随访">
      <Tabs
        defaultActiveKey="todo"
        items={[
          {
            key: 'todo', label: `待随访${pending.length ? `(${pending.length})` : ''}`,
            children: pending.length ? pending.map(renderFollowup)
              : <Empty description="暂无待随访问卷" />,
          },
          {
            key: 'done', label: '已填写',
            children: done.length ? done.map(renderFollowup)
              : <Empty description="暂无已填写问卷" />,
          },
          {
            key: 'notify', label: '提醒通知',
            children: (
              <>
                <div style={{ textAlign: 'right', marginBottom: 8 }}>
                  <Button size="small" onClick={readAll}>全部已读</Button>
                </div>
                {notifs.length ? notifs.map(n => (
                  <div key={n.id} style={{
                    display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 12px',
                    borderBottom: '1px solid var(--color-border-light)',
                    background: n.is_read ? 'transparent' : 'var(--color-primary-light)',
                    borderRadius: 'var(--radius-sm)', marginBottom: 6,
                    cursor: 'pointer',
                  }} onClick={async () => {
                    if (!n.is_read) { await api.post(`/notifications/${n.id}/read`); refreshBadge(); load(); }
                  }}>
                    <span style={{ fontSize: 13 }}>{n.is_read ? '✓' : '●'}</span>
                    <span style={{ fontSize: 13 }}>{n.title}</span>
                  </div>
                )) : <Empty description="暂无通知" />}
              </>
            ),
          },
        ]}
      />
    </Layout>
  );
}
```

- [ ] **Step 2: 问卷填写页**

创建 `frontend/packages/user-portal/src/pages/FollowUpQuestionnairePage.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Spin, Button, Radio, Checkbox, Input, Tag, message } from 'antd';
import Layout from '../components/Layout';
import { useUserStore } from '../stores/userStore';

interface Question {
  id: number; question_type: 'single' | 'multiple' | 'text';
  question_text: string; options: string[]; is_required: boolean;
  answer: any; sort_order: number;
}

interface Detail {
  id: number; status: 'pending' | 'completed'; overall_level: string;
  template_name: string | null; questions: Question[];
  submitted_at: string | null;
}

export default function FollowUpQuestionnairePage() {
  const { id } = useParams();
  const { api } = useUserStore();
  const nav = useNavigate();
  const [data, setData] = useState<Detail | null>(null);
  const [answers, setAnswers] = useState<Record<number, any>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.get(`/followup/${id}`).then(r => {
      setData(r.data);
      const init: Record<number, any> = {};
      (r.data.questions || []).forEach(q => {
        if (q.answer !== null && q.answer !== undefined) init[q.id] = q.answer;
      });
      setAnswers(init);
    }).catch(() => nav('/followup'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (!data) return <Layout title="随访问卷"><div style={{ textAlign: 'center', padding: 60 }}><Spin /></div></Layout>;

  const submit = async () => {
    const missing = (data.questions || []).find(q => q.is_required &&
      (answers[q.id] === undefined || answers[q.id] === null || answers[q.id] === '' ||
       (Array.isArray(answers[q.id]) && answers[q.id].length === 0)));
    if (missing) { message.warning(`请填写必答题:${missing.question_text}`); return; }
    setSubmitting(true);
    try {
      const list = (data.questions || []).map(q => ({
        question_id: q.id,
        answer: q.question_type === 'multiple' ? (answers[q.id] || []) : (answers[q.id] ?? ''),
      }));
      await api.post(`/followup/${data.id}/submit`, { answers: list });
      message.success('问卷已提交');
      nav('/followup');
    } catch (e: any) {
      message.error(e.response?.data?.detail || '提交失败');
    } finally { setSubmitting(false); }
  };

  const readonly = data.status === 'completed';

  return (
    <Layout title="随访问卷">
      <div style={{ marginBottom: 12 }}>
        <Tag color={data.overall_level === 'red' ? 'red' : 'gold'}>{data.overall_level}</Tag>
        {data.submitted_at && <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginLeft: 8 }}>已提交 {data.submitted_at}</span>}
      </div>
      {(data.questions || []).map(q => (
        <div key={q.id} style={{
          background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
          padding: '14px 16px', boxShadow: 'var(--shadow-sm)',
          border: '1px solid var(--color-border-light)', marginBottom: 12,
        }}>
          <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 8 }}>
            {q.is_required && <span style={{ color: 'var(--color-red)' }}>* </span>}
            {q.question_text}
          </div>
          {q.question_type === 'single' && (
            <Radio.Group disabled={readonly} value={answers[q.id]}
              onChange={e => setAnswers({ ...answers, [q.id]: e.target.value })}>
              {(q.options || []).map(o => <Radio key={o} value={o}>{o}</Radio>)}
            </Radio.Group>
          )}
          {q.question_type === 'multiple' && (
            <Checkbox.Group disabled={readonly} value={answers[q.id] || []}
              onChange={v => setAnswers({ ...answers, [q.id]: v })}>
              {(q.options || []).map(o => <Checkbox key={o} value={o}>{o}</Checkbox>)}
            </Checkbox.Group>
          )}
          {q.question_type === 'text' && (
            <Input.TextArea disabled={readonly} rows={3}
              value={answers[q.id] ?? ''}
              onChange={e => setAnswers({ ...answers, [q.id]: e.target.value })} />
          )}
        </div>
      ))}
      {!readonly && (
        <Button type="primary" block loading={submitting} onClick={submit}
          style={{ marginTop: 8 }}>提交问卷</Button>
      )}
    </Layout>
  );
}
```

- [ ] **Step 3: 加路由**

修改 `frontend/packages/user-portal/src/router.tsx`:第 8 行后 import 两个页面:

```tsx
import FollowUpCenterPage from './pages/FollowUpCenterPage';
import FollowUpQuestionnairePage from './pages/FollowUpQuestionnairePage';
```

并在 `/chat/:sessionId` 路由之后加:

```tsx
    <Route path="/followup" element={<AuthGuard><FollowUpCenterPage /></AuthGuard>} />
    <Route path="/followup/:id" element={<AuthGuard><FollowUpQuestionnairePage /></AuthGuard>} />
```

- [ ] **Step 4: 类型检查**

Run: `cd frontend && pnpm exec tsc -p packages/user-portal/tsconfig.json --noEmit`
Expected: 无输出(通过)。

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/user-portal/src/pages/FollowUpCenterPage.tsx frontend/packages/user-portal/src/pages/FollowUpQuestionnairePage.tsx frontend/packages/user-portal/src/router.tsx
git commit -m "feat(user-portal): 随访中心 + 随访问卷页 + 路由"
```

---

### Task 10: doctor-portal —— 报告详情随访区块

**Files:**
- Create: `frontend/packages/doctor-portal/src/components/FollowupPanel.tsx`
- Modify: `frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx`

**Interfaces:**
- Consumes `useDoctorStore`(api)。Produces `FollowupPanel({ reportId }: { reportId: number })`。

- [ ] **Step 1: 写面板组件**

创建 `frontend/packages/doctor-portal/src/components/FollowupPanel.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Card, Tag, Spin, Empty } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';

interface Q { id: number; question_type: string; question_text: string; options: string[]; answer: any; }

interface FollowupData {
  id: number; report_id: number; status: string; overall_level: string;
  generated_at: string | null; submitted_at: string | null; template_name: string | null;
  questions: Q[];
}

const COLOR: any = { red: 'red', yellow: 'gold', green: 'green' };

export default function FollowupPanel({ reportId }: { reportId: number }) {
  const { api } = useDoctorStore();
  const [data, setData] = useState<FollowupData | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setLoaded(false);
    api.get(`/followup/by-report/${reportId}`).then(r => setData(r.data)).catch(() => setData(null))
      .finally(() => setLoaded(true));
  }, [reportId, api]);

  if (!loaded) return <Card title="随访问卷" loading style={{ marginBottom: 16 }} />;
  if (!data) return null; // 未触发随访,不占版面

  const label = (q: Q) => q.answer !== null && q.answer !== undefined
    ? (Array.isArray(q.answer) ? (q.answer as string[]).join('、') : String(q.answer))
    : '—';

  return (
    <Card title="随访问卷" style={{ marginBottom: 16 }}>
      <div style={{ marginBottom: 12 }}>
        <Tag color={data.status === 'completed' ? 'green' : 'orange'}>
          {data.status === 'completed' ? '已填写' : '待填写'}
        </Tag>
        {data.overall_level && <Tag color={COLOR[data.overall_level]}>{data.overall_level}</Tag>}
        {data.submitted_at && <span style={{ fontSize: 12, color: '#888', marginLeft: 8 }}>提交于 {data.submitted_at}</span>}
      </div>
      {data.questions.length === 0 ? <Empty description="问卷为空" /> : data.questions.map(q => (
        <div key={q.id} style={{ padding: '6px 0', borderBottom: '1px solid #f0f0f0' }}>
          <div style={{ fontWeight: 500, fontSize: 13 }}>{q.question_text}</div>
          <div style={{ fontSize: 13, color: '#555', marginTop: 2 }}>{label(q)}</div>
        </div>
      ))}
    </Card>
  );
}
```

- [ ] **Step 2: 接入 ReportDetailPage**

修改 `frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx`:
- import(第 6 行后)加:

```tsx
import FollowupPanel from '../components/FollowupPanel';
```

- 在 `InterpretationReportCard`(第 88-93 行)之后、`</DoctorLayout>` 之前加:

```tsx
      <FollowupPanel reportId={Number(id)} />
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && pnpm exec tsc -p packages/doctor-portal/tsconfig.json --noEmit`
Expected: 无输出(通过)。

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/doctor-portal/src/components/FollowupPanel.tsx frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx
git commit -m "feat(doctor-portal): 报告详情随访问卷区块(状态 + 答卷)"
```

---

### Task 11: admin-portal —— 平台随访问卷模板维护页

**Files:**
- Create: `frontend/packages/admin-portal/src/api/followupTemplate.ts`
- Create: `frontend/packages/admin-portal/src/pages/FollowupTemplatePage.tsx`
- Modify: `frontend/packages/admin-portal/src/components/AppLayout.tsx`(菜单)
- Modify: `frontend/packages/admin-portal/src/router.tsx`(路由)

**Interfaces:**
- Produces 路由 `/followup-template`(平台管理员维护模板)。

- [ ] **Step 1: API 封装**

创建 `frontend/packages/admin-portal/src/api/followupTemplate.ts`:

```typescript
import { useAdminStore } from "../stores/adminStore";

const api = () => useAdminStore.getState().api;

export interface TemplateQuestion {
  id?: number | null;
  question_type: "single" | "multiple" | "text";
  question_text: string;
  options: string[];
  is_required: boolean;
  sort_order: number;
  is_active: boolean;
}

export interface FollowupTemplate {
  id: number | null;
  name: string;
  description: string | null;
  questions: TemplateQuestion[];
}

export async function getTemplate(): Promise<FollowupTemplate> {
  const r = await api().get("/followup/template");
  return r.data;
}

export async function saveTemplate(t: FollowupTemplate): Promise<FollowupTemplate> {
  const r = await api().put("/followup/template", t);
  return r.data;
}
```

- [ ] **Step 2: 模板维护页**

创建 `frontend/packages/admin-portal/src/pages/FollowupTemplatePage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Card, Input, Select, Checkbox, Button, Space, message, Typography } from "antd";
import { PlusOutlined, UpOutlined, DownOutlined, DeleteOutlined } from "@ant-design/icons";
import { getTemplate, saveTemplate, TemplateQuestion, FollowupTemplate } from "../api/followupTemplate";

const { Text } = Typography;
const TYPE_OPTIONS = [
  { value: "single", label: "单选" },
  { value: "multiple", label: "多选" },
  { value: "text", label: "文本填空" },
];

export default function FollowupTemplatePage() {
  const [tpl, setTpl] = useState<FollowupTemplate>({ id: null, name: "通用检后随访", description: null, questions: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getTemplate().then(t => {
      const base = t.id ? t : { ...t, name: "通用检后随访" };
      setTpl({ ...base, questions: (t.questions || []).length ? t.questions : [] });
    }).finally(() => setLoading(false));
  }, []);

  const patchQ = (idx: number, patch: Partial<TemplateQuestion>) => {
    setTpl(s => ({ ...s, questions: s.questions.map((q, i) => i === idx ? { ...q, ...patch } : q) }));
  };

  const add = () => setTpl(s => ({
    ...s,
    questions: [...s.questions, {
      question_type: "text", question_text: "", options: [],
      is_required: true, sort_order: s.questions.length, is_active: true,
    }],
  }));

  const remove = (idx: number) => setTpl(s => ({ ...s, questions: s.questions.filter((_, i) => i !== idx) }));

  const move = (idx: number, dir: -1 | 1) => setTpl(s => {
    const arr = [...s.questions];
    const j = idx + dir;
    if (j < 0 || j >= arr.length) return s;
    [arr[idx], arr[j]] = [arr[j], arr[idx]];
    arr.forEach((q, i) => { q.sort_order = i; });
    return { ...s, questions: arr };
  });

  const save = async () => {
    if (!tpl.questions.length) { message.error("激活模板不允许空题目"); return; }
    const bad = tpl.questions.findIndex(q =>
      !q.question_text.trim() ||
      ((q.question_type === "single" || q.question_type === "multiple") &&
        (!q.options || q.options.filter(o => o.trim()).length === 0)));
    if (bad >= 0) { message.error(`第 ${bad + 1} 题缺少题干或选项`); return; }
    setSaving(true);
    try {
      const saved = await saveTemplate({
        ...tpl,
        questions: tpl.questions.map((q, i) => ({ ...q, sort_order: i, options: q.question_type === "text" ? [] : (q.options || []).filter(o => o.trim()) })),
      });
      setTpl(saved);
      message.success("模板已保存,后续新解读将套用");
    } catch (e: any) {
      message.error(e.response?.data?.detail || "保存失败");
    } finally { setSaving(false); }
  };

  if (loading) return <div style={{ textAlign: "center", padding: 48 }}>加载中…</div>;

  return (
    <div style={{ maxWidth: 900, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>随访问卷模板</h2>
          <Text type="secondary">平台统一维护的单套激活模板;红/黄报告解读完成后按此快照生成问卷</Text>
        </div>
        <Button type="primary" loading={saving} onClick={save}>保存模板</Button>
      </div>

      <Card>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space>
            <span>模板名:</span>
            <Input style={{ width: 240 }} value={tpl.name}
              onChange={e => setTpl({ ...tpl, name: e.target.value })} />
          </Space>
          {tpl.questions.map((q, idx) => (
            <div key={idx} style={{ border: "1px solid #f0f0f0", borderRadius: 8, padding: 12, background: "#FAFAF8" }}>
              <Space style={{ marginBottom: 8 }}>
                <Button size="small" icon={<UpOutlined />} onClick={() => move(idx, -1)} />
                <Button size="small" icon={<DownOutlined />} onClick={() => move(idx, 1)} />
                <Select size="small" value={q.question_type} style={{ width: 110 }}
                  options={TYPE_OPTIONS}
                  onChange={v => patchQ(idx, { question_type: v, options: v === "text" ? [] : q.options })} />
                <Checkbox checked={q.is_required} onChange={e => patchQ(idx, { is_required: e.target.checked })}>必填</Checkbox>
                <Checkbox checked={q.is_active} onChange={e => patchQ(idx, { is_active: e.target.checked })}>启用</Checkbox>
                <Button size="small" danger icon={<DeleteOutlined />} onClick={() => remove(idx)} />
              </Space>
              <Input placeholder="题干(如:近期是否头晕?)" value={q.question_text}
                onChange={e => patchQ(idx, { question_text: e.target.value })} />
              {q.question_type !== "text" && (
                <Input.TextArea rows={2} placeholder="每行一个选项" value={q.options.join("\n")}
                  onChange={e => patchQ(idx, { options: e.target.value.split("\n") })} />
              )}
            </div>
          ))}
          <Button block icon={<PlusOutlined />} onClick={add}>添加题目</Button>
        </Space>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: 菜单 + 路由**

`frontend/packages/admin-portal/src/components/AppLayout.tsx`:
- import 区第 3 行改 `@ant-design/icons` 为加 `ProfileOutlined`:

```tsx
import { DashboardOutlined, BarChartOutlined, LogoutOutlined, ProfileOutlined } from '@ant-design/icons';
```

- `menuItems` 数组加一项:

```tsx
    { key: 'followup-template', icon: <ProfileOutlined />, label: '随访问卷模板', onClick: () => navigate('/followup-template') },
```

- `currentKey` 逻辑改为:

```tsx
  const currentKey = location.pathname === '/group-analysis' ? 'group-analysis'
    : location.pathname === '/followup-template' ? 'followup-template' : 'dashboard';
```

`frontend/packages/admin-portal/src/router.tsx`:
- import(第 5 行后)加:

```tsx
import FollowupTemplatePage from './pages/FollowupTemplatePage';
```

- 在 `/group-analysis` 路由后加:

```tsx
      <Route path="/followup-template" element={<FollowupTemplatePage />} />
```

- [ ] **Step 4: 类型检查**

Run: `cd frontend && pnpm exec tsc -p packages/admin-portal/tsconfig.json --noEmit`
Expected: 无输出(通过)。

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/admin-portal/src/api/followupTemplate.ts frontend/packages/admin-portal/src/pages/FollowupTemplatePage.tsx frontend/packages/admin-portal/src/components/AppLayout.tsx frontend/packages/admin-portal/src/router.tsx
git commit -m "feat(admin-portal): 平台随访问卷模板维护页 + 菜单/路由"
```

---

### Task 12: 文档 —— app-integration-guide 增补 + AGENTS.md 工程记忆

**Files:**
- Modify: `docs/app-integration-guide.md`
- Modify: `AGENTS.md`

**Interfaces:** 无代码接口,只改文档。

- [ ] **Step 1: app-integration-guide.md 增补随访章节**

在 `docs/app-integration-guide.md` 第 8 节(健康画像 profile)之后插入「8.5 检后随访与提醒」,并把 §10 映射表加一行:

```markdown
## 8.5 检后随访与提醒(followup)

解读完成后,若报告整体风险为红/黄,系统自动为该报告生成随访问卷与一条复查提醒
(站内通知)。问卷/通知**均按报告各建各的**,只在解读完成时生成一次;无后台推送,
App 用下述接口轮询/拉取即可。

| 接口 | 说明 |
|------|------|
| `GET /followup/center?page=&page_size=` | 我的随访列表(含 pending/completed、overall_level、需复查指标清单),附 `has_pending` |
| `GET /followup/{id}` | 随访问卷表单(逐题 type: single/multiple/text + options + is_required;已提交带 answer) |
| `POST /followup/{id}/submit` | 提交答卷 `{answers:[{question_id, answer}]}`;single 传选项标签、multiple 传标签数组、text 传字符串;必填/选项非法/重复提交返回 400 |
| `GET /notifications?unread_only=` | 我的通知列表(新→旧,含 title/content/is_read) |
| `GET /notifications/unread-count` | `{unread_count}` 红点轮询 |
| `POST /notifications/{id}/read` | 单条已读 |
| `POST /notifications/read-all` | 全部已读 |

> 通知 `content` 结构:`{report_id, report_date, overall_level, followup_pending, recheck_indicators:[{item_name, result_value, unit, ref_range, color_level}]}`,
> App 可直接渲染指标清单。
```

并在 §10 映射表加行:

```markdown
| FollowUpCenterPage / 问卷填写 | `GET /followup/center`、`GET /followup/{id}`、`POST /followup/{id}/submit`、`GET /notifications*`、`POST /notifications/*/read` |
```

- [ ] **Step 2: AGENTS.md 增补工程记忆**

在 AGENTS.md「新 tenant 初始化必读」的表清单中,表格后追加一段(仅记录本次落地事实,表述与 DDL 一致):

```markdown
## 检后随访表(2026-09-07 起)

新表 5 张,同样须在三处 DDL 源保持一致:`infra/mysql/init/01_template_db.sql`(平台库
`followup_template` / `followup_template_question`,平台统一维护单套激活模板)、
`start.sh` DDL 块与 `infra/mysql/init/02_hospital_created.sql` 存储过程(租户库
`followup` / `followup_question` / `user_notification`)。存量 5 库迁移见
`backend/scripts/manual_migrations/006_followup.sql`。

- 触发:解读 worker(`interpretation/worker.py`)在 `run_interpretation_agent` 成功后同步调
  `try_generate_followup`;仅 `overall_level in (red,yellow)` 且此前无该 report_id 随访时生成。
- 生成把平台激活模板问题 + 黄/红指标(`indicator_judgment.color_level`)快照进租户库
  `followup_question` / `followup.recheck_indicators_json`,并写一条 `user_notification`
  (`category=recheck_reminder`),同事务;无激活模板时跳过(记 `app.followup`)。
- 用户侧接口在 `backend/app/modules/followup/router.py`,前缀 `/api/v1/followup` 与
  `/api/v1/notifications`;全部 `role='user'` + 双锚定,App(app-login)直接可用,无真推送。
- 删除报告时 `report/router.py::delete_report` 调 `delete_report_followup` 清理三张关联表。
```

- [ ] **Step 3: 全量回归(后端 + 前端类型)**

Run:
```
cd backend && .venv/bin/python -m pytest -q tests/followup tests/test_interp_worker_bulk.py tests/test_main_wiring.py
cd ../frontend && pnpm exec tsc -p packages/user-portal/tsconfig.json --noEmit && pnpm exec tsc -p packages/doctor-portal/tsconfig.json --noEmit && pnpm exec tsc -p packages/admin-portal/tsconfig.json --noEmit
```
Expected: 全部通过/无输出。

- [ ] **Step 4: Commit**

```bash
git add docs/app-integration-guide.md AGENTS.md
git commit -m "docs: 检后随访 App 对接章节 + AGENTS 工程记忆(新表/触发/删除联动)"
```

---

## 验证收尾

全部任务完成后:
- 后端:`cd backend && .venv/bin/python -m pytest -q tests/followup`(≥30 passed)
- 存量库执行 `backend/scripts/manual_migrations/006_followup.sql`(平台库 + hospital_H001-H004/hospital_1)
- admin-portal 平台管理员配好一套激活模板 → 触发一份黄/红解读 → user-portal 随访 tab 出现提醒与问卷 → 填写提交 → doctor-portal 报告详情可见答卷;绿报告不产生随访与通知
