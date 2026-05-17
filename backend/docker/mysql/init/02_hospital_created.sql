USE hospital_template;

DELIMITER //

CREATE PROCEDURE IF NOT EXISTS create_hospital_database(IN p_hospital_id VARCHAR(32))
BEGIN
    SET @db_name = CONCAT('hospital_', p_hospital_id);
    SET @sql = CONCAT('CREATE DATABASE IF NOT EXISTS `', @db_name,
        '` DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci');
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;

    SET @sql = CONCAT('USE `', @db_name, '`');
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;

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

    CREATE TABLE IF NOT EXISTS knowledge_category (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        parent_id BIGINT DEFAULT NULL,
        sort_order INT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

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

    CREATE TABLE IF NOT EXISTS report_template (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        type VARCHAR(10) NOT NULL,
        content LONGBLOB DEFAULT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS statistic_cache (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stat_type VARCHAR(50) NOT NULL,
        params_hash VARCHAR(64) NOT NULL,
        result_json JSON DEFAULT NULL,
        expired_at DATETIME DEFAULT NULL
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS dispatch_config (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        config_key VARCHAR(50) NOT NULL,
        config_value VARCHAR(500) NOT NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

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
