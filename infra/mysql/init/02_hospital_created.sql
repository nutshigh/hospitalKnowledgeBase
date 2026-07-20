USE hospital_template;

DROP PROCEDURE IF EXISTS create_hospital_database;

DELIMITER //
CREATE PROCEDURE create_hospital_database(IN p_hospital_id VARCHAR(32))
BEGIN
    -- 注意:MySQL 不支持在 PREPARE 语句中使用 USE (错误 1295),
    -- 因此本 SP 不切默认库,而是用 `<db>`.`<table>` 全限定名显式建表。
    -- 调用方需在 hospital_template 库内 CALL 即可。

    SET @db_name = CONCAT('hospital_', p_hospital_id);

    -- 1. 建库
    SET @sql = CONCAT('CREATE DATABASE IF NOT EXISTS `', @db_name,
        '` DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    -- 2. 全限定建表。DDL 与 start.sh 内 hospital_H001 初始化块保持一致,
    --    且已合并 manual_migrations/001 与 /002 的增量列,
    --    新租户一建出来即等于 hospital_H001 当前态,无需再跑 manual migrations。
    --    全部使用 CREATE TABLE IF NOT EXISTS,保证可重复 CALL。

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.hospital_user ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, '
        'name VARCHAR(50), phone VARCHAR(20), gender VARCHAR(5), age INT, '
        'unit_name VARCHAR(100), '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.knowledge_category ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, '
        'name VARCHAR(100) NOT NULL, parent_id BIGINT DEFAULT NULL, '
        'sort_order INT NOT NULL DEFAULT 0, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.knowledge_entry ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, category_id BIGINT DEFAULT NULL, '
        'title VARCHAR(200) NOT NULL, content TEXT NOT NULL, '
        'source_type VARCHAR(20) NOT NULL DEFAULT ''manual'', '
        'source_file VARCHAR(500) DEFAULT NULL, '
        'chunk_index INT NOT NULL DEFAULT 0, parent_entry_id BIGINT DEFAULT NULL, '
        'vector_id VARCHAR(64) DEFAULT NULL, status TINYINT NOT NULL DEFAULT 1, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.report_task ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, '
        'original_file_path VARCHAR(500) NOT NULL, original_filename VARCHAR(200) NOT NULL, '
        'file_type VARCHAR(10) NOT NULL, file_size BIGINT NOT NULL DEFAULT 0, '
        'thumbnail_path VARCHAR(500) DEFAULT NULL, status VARCHAR(20) NOT NULL DEFAULT ''queued'', '
        'priority TINYINT NOT NULL DEFAULT 0, retry_count INT NOT NULL DEFAULT 0, '
        'error_message TEXT DEFAULT NULL, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, '
        'completed_at DATETIME DEFAULT NULL'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.report_info ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, task_id BIGINT DEFAULT NULL, '
        'user_id BIGINT NOT NULL, name VARCHAR(50), gender VARCHAR(5), age INT, '
        'report_date DATE, check_type VARCHAR(20), unit_name VARCHAR(100), '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.report_indicator ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, '
        'item_name VARCHAR(100) NOT NULL, item_name_standard VARCHAR(100) DEFAULT NULL, '
        'item_code VARCHAR(50) DEFAULT NULL, result_value VARCHAR(50) DEFAULT NULL, '
        'unit VARCHAR(20) DEFAULT NULL, ref_range_low VARCHAR(50) DEFAULT NULL, '
        'ref_range_high VARCHAR(50) DEFAULT NULL, category VARCHAR(50) DEFAULT NULL, '
        'raw_text TEXT DEFAULT NULL'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.report_interpretation ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, report_id BIGINT NOT NULL, '
        'overall_level VARCHAR(10) DEFAULT NULL, red_count INT NOT NULL DEFAULT 0, '
        'yellow_count INT NOT NULL DEFAULT 0, green_count INT NOT NULL DEFAULT 0, '
        'summary_text TEXT DEFAULT NULL, summary_refs JSON DEFAULT NULL, '
        'comparison_summary TEXT DEFAULT NULL, comparison_baseline_id BIGINT DEFAULT NULL, '
        'quality_note VARCHAR(255) DEFAULT NULL, '
        'status VARCHAR(20) NOT NULL DEFAULT ''pending'', retry_count INT NOT NULL DEFAULT 0, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'completed_at DATETIME DEFAULT NULL'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.indicator_judgment ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, interpretation_id BIGINT NOT NULL, '
        'indicator_id BIGINT NOT NULL, item_name VARCHAR(100) NOT NULL, '
        'result_value VARCHAR(50) DEFAULT NULL, deviation VARCHAR(10) DEFAULT NULL, '
        'color_level VARCHAR(10) DEFAULT NULL, matched_rule_id BIGINT DEFAULT NULL, '
        'explanation TEXT DEFAULT NULL, suggestion TEXT DEFAULT NULL, '
        'knowledge_refs JSON DEFAULT NULL, certainty VARCHAR(10) DEFAULT NULL, '
        'certainty_reason TEXT DEFAULT NULL'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.triage_rule ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, rule_name VARCHAR(100) NOT NULL, '
        'rule_type VARCHAR(20) NOT NULL, indicator_code VARCHAR(50) DEFAULT NULL, '
        'conditions JSON NOT NULL, color_level VARCHAR(10) NOT NULL, '
        'priority INT NOT NULL DEFAULT 0, is_active TINYINT NOT NULL DEFAULT 1, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.report_template ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL, '
        'type VARCHAR(10) NOT NULL, content LONGBLOB DEFAULT NULL, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.statistic_cache ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, stat_type VARCHAR(50) NOT NULL, '
        'params_hash VARCHAR(64) NOT NULL, result_json JSON DEFAULT NULL, '
        'expired_at DATETIME DEFAULT NULL'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.dispatch_config ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, config_key VARCHAR(50) NOT NULL, '
        'config_value VARCHAR(500) NOT NULL, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.resource_metric ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, '
        'metric_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'cpu_percent DECIMAL(5,1) DEFAULT NULL, memory_percent DECIMAL(5,1) DEFAULT NULL, '
        'gpu_percent DECIMAL(5,1) DEFAULT NULL, gpu_memory_percent DECIMAL(5,1) DEFAULT NULL, '
        'queue_depth INT DEFAULT NULL, active_workers INT DEFAULT NULL'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.chat_session ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, '
        'hospital_id VARCHAR(32) NOT NULL, report_id BIGINT DEFAULT NULL, '
        'title VARCHAR(200) DEFAULT NULL, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.chat_message ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, session_id BIGINT NOT NULL, '
        'role VARCHAR(10) NOT NULL, content TEXT NOT NULL, '
        'knowledge_refs JSON DEFAULT NULL, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'FOREIGN KEY (session_id) REFERENCES `', @db_name, '`.chat_session(id)'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.batch_import ('
        'id VARCHAR(36) PRIMARY KEY, hospital_id VARCHAR(32) NOT NULL, '
        'user_id VARCHAR(64) NOT NULL, filename VARCHAR(255) NOT NULL, '
        'archive_path VARCHAR(512) NOT NULL, '
        'total BIGINT NOT NULL DEFAULT 0, parsed_ok BIGINT NOT NULL DEFAULT 0, '
        'interp_ok BIGINT NOT NULL DEFAULT 0, failed BIGINT NOT NULL DEFAULT 0, '
        'status VARCHAR(24) NOT NULL DEFAULT ''uploading'', error_message TEXT, '
        'created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at DATETIME, '
        'updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, '
        'KEY idx_batch_status (status), KEY idx_batch_hospital (hospital_id)'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('CREATE TABLE IF NOT EXISTS `', @db_name, '`.batch_import_file ('
        'id VARCHAR(36) PRIMARY KEY, batch_id VARCHAR(36) NOT NULL, '
        'file_path VARCHAR(512) NOT NULL, file_size BIGINT NOT NULL DEFAULT 0, '
        'crc32 VARCHAR(8) NOT NULL, status VARCHAR(24) NOT NULL DEFAULT ''queued'', '
        'failed_stage VARCHAR(24) DEFAULT NULL, report_task_id BIGINT, '
        'error_message TEXT, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, '
        'UNIQUE KEY uq_batch_file (batch_id, crc32), KEY idx_bfile_status (status), '
        'CONSTRAINT fk_bfile_batch FOREIGN KEY (batch_id) '
        'REFERENCES `', @db_name, '`.batch_import(id)'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4');
    PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
END//
DELIMITER ;