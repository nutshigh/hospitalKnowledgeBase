-- 005: batch_import_file.dispatch_hospital — 跨院分发:记录每个文件的目标医院
-- 用法:对每个存量 tenant 库(hospital_<id>)执行。
-- 背景:批量上传文件名解析出目标医院(orgId),报告 task/report 写入该医院库;而
--      BatchImportFile/BatchImport 行始终写在上传方(批次)库。parsing/解读 worker
--      需要知道文件分发到了哪个库,才能把 parsed_ok/interp_ok/failed 进度记回批次库、
--      以及重试时定位目标库里的 report_task。dispatch_hospital 存的就是这个目标医院。
--      单医院场景(目标==上传方)留 NULL 即可,retry 会回退到批次 hospital_id。
-- 注意:本机 MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS(MariaDB 语法),需先查列存在。

-- 每个 hospital_<id> 库:
ALTER TABLE batch_import_file ADD COLUMN dispatch_hospital VARCHAR(24) DEFAULT NULL COMMENT '文件解析出的目标医院(跨院分发时≠批次hospital_id)';
