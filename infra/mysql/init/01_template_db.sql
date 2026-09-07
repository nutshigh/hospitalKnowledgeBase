CREATE DATABASE IF NOT EXISTS hospital_template
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE hospital_template;

CREATE TABLE IF NOT EXISTS hospital_tenant (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    hospital_id VARCHAR(32) NOT NULL UNIQUE COMMENT '医院唯一标识',
    hospital_name VARCHAR(100) NOT NULL COMMENT '医院名称',
    db_name VARCHAR(64) NOT NULL COMMENT '对应数据库名',
    is_active TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS platform_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(50) DEFAULT NULL COMMENT '登录姓名(与报告文件名姓名段一致)',
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL COMMENT 'user / doctor / admin',
    hospital_id VARCHAR(32) DEFAULT NULL COMMENT '医生/用户关联的医院',
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS id_card_suffix VARCHAR(8) NULL COMMENT '身份证后六位(终端用户锚定)';
-- 三元组唯一索引,关闭注册的 TOCTOU 竞态(MySQL 唯一索引允许多个 NULL,存量行不冲突)
ALTER TABLE platform_user ADD UNIQUE INDEX uq_platform_user_anchor (hospital_id, name, id_card_suffix);

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
