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
