-- 003: 批量上传按身份证后六位分发(存量库迁移)
-- 用法:对每个存量 tenant 库(hospital_<id>)与 hospital_template 分别执行。

-- 每个 hospital_<id> 库:
ALTER TABLE report_task MODIFY user_id VARCHAR(16) NOT NULL;
ALTER TABLE report_info MODIFY user_id VARCHAR(16) NOT NULL;
ALTER TABLE chat_session MODIFY user_id VARCHAR(16) NOT NULL;
ALTER TABLE chat_session ADD COLUMN IF NOT EXISTS name VARCHAR(50) NULL;

-- hospital_template 库:
ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS id_card_suffix VARCHAR(8) NULL;
ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS name VARCHAR(50) NULL COMMENT '登录姓名(与报告文件名姓名段一致)';
-- 三元组唯一索引,关闭注册/上传的 TOCTOU 竞态(MySQL 唯一索引允许多个 NULL,存量行不冲突)
ALTER TABLE platform_user ADD UNIQUE INDEX uq_platform_user_anchor (hospital_id, name, id_card_suffix);
