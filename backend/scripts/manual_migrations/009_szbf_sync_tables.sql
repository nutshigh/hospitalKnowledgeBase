-- backend/scripts/manual_migrations/009_szbf_sync_tables.sql
-- 执行库：每个需要采集数据同步的 hospital_<id> 库（首期 hospital_133）
-- 幂等：CREATE TABLE IF NOT EXISTS
CREATE TABLE IF NOT EXISTS szbf_sync_map (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  source_user_id VARCHAR(32) NOT NULL,
  exam_code VARCHAR(64) NOT NULL,
  report_id BIGINT NOT NULL,
  source_updated_at VARCHAR(32) DEFAULT NULL,
  raw_conclusion MEDIUMTEXT,
  raw_con_suggest MEDIUMTEXT,
  synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_szbf_exam (source_user_id, exam_code),
  KEY idx_szbf_report (report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS szbf_sync_state (
  source_key VARCHAR(32) PRIMARY KEY,
  last_detail_update VARCHAR(32) DEFAULT NULL,
  last_conclusion_update VARCHAR(32) DEFAULT NULL,
  last_run_at DATETIME DEFAULT NULL,
  status VARCHAR(16) DEFAULT NULL,
  message TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
