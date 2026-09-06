-- 004: report_info.parsed_name — 展示名与归属分离(存量库迁移)
-- 用法:对每个存量 tenant 库(hospital_<id>)执行。
-- 背景:report_info.name 承担「归属锚定」(过滤)与「展示」双职责。单份上传时归属
--      锚定名=登录账号名(如 测试1),而 PDF 解析出的真实姓名(如 孙越锋)应作为展示名,
--      故拆出 parsed_name 列(仅展示)。列表/详情的 name 字段返回 parsed_name or name,
--      归属过滤仍按 name 双锚定。
-- 注意:本机 MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS(MariaDB 语法),需先查列存在。

-- 每个 hospital_<id> 库:
ALTER TABLE report_info ADD COLUMN parsed_name VARCHAR(50) NULL COMMENT '解析出的报告真实姓名(仅展示)';

-- 存量数据回填参考(parsed_name 源自 PDF/文件名真实姓名,需按报告实际情况人工回填):
-- UPDATE report_info SET parsed_name = <真实姓名> WHERE id = <report_id>;
