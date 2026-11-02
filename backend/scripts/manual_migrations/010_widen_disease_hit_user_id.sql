-- backend/scripts/manual_migrations/010_widen_disease_hit_user_id.sql
-- 执行库：每个需要采集数据同步、且 disease_hit 仍是 BIGINT 的 hospital_<id> 库
-- 幂等：MODIFY COLUMN 可重复执行
ALTER TABLE disease_hit MODIFY COLUMN user_id VARCHAR(64) NOT NULL;
