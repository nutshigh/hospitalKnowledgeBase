-- 003_add_disease_mapping.sql
-- 功能：新增"指标-疾病映射表"，支撑统计分析（慢性病/重大疾病分类、疾病谱、疾病类别分布）
-- 说明：
-- 1. 本脚本对"每个医院库"（hospital_H001、hospital_H002 ……）分别执行；
-- 2. 表为纯新增，不影响既有表与接口；统计端点通过 stat_mode=indicator 可绕过本表（保留指标直统）；
-- 3. 种子数据仅为演示示例，正式病种清单需业务方确认后灌入；
-- 4. 回滚：DROP TABLE IF EXISTS disease_mapping;

CREATE TABLE IF NOT EXISTS disease_mapping (
  id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  item_name_standard VARCHAR(200) NOT NULL COMMENT '标准指标名（关联 indicator_judgment.item_name / report_indicator.item_name_standard）',
  item_name VARCHAR(200) DEFAULT NULL COMMENT '原始指标名（兼容未标准化场景，可空）',
  disease_name VARCHAR(200) NOT NULL COMMENT '归一疾病名（如：高血压）',
  disease_category VARCHAR(20) NOT NULL DEFAULT 'OTHER' COMMENT '疾病分类：CHRONIC-慢性病，MAJOR-重大疾病，OTHER-其他',
  disease_class VARCHAR(100) DEFAULT NULL COMMENT '疾病类别/所属系统（如：心血管系统、内分泌代谢）',
  sort_code INT DEFAULT 100 COMMENT '排序号',
  enabled TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用：1启用 0停用',
  create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_item_standard (item_name_standard),
  KEY idx_disease (disease_category, disease_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指标-疾病映射表（统计分析用）';

-- ===== 演示用种子数据（示例，正式清单以业务方确认为准） =====
INSERT INTO disease_mapping (item_name_standard, disease_name, disease_category, disease_class, sort_code) VALUES
 ('血压', '高血压', 'CHRONIC', '心血管系统', 1),
 ('收缩压', '高血压', 'CHRONIC', '心血管系统', 2),
 ('舒张压', '高血压', 'CHRONIC', '心血管系统', 3),
 ('空腹血糖', '糖尿病', 'CHRONIC', '内分泌代谢', 4),
 ('糖化血红蛋白', '糖尿病', 'CHRONIC', '内分泌代谢', 5),
 ('总胆固醇', '血脂异常', 'CHRONIC', '内分泌代谢', 6),
 ('甘油三酯', '血脂异常', 'CHRONIC', '内分泌代谢', 7),
 ('低密度脂蛋白胆固醇', '血脂异常', 'CHRONIC', '内分泌代谢', 8),
 ('尿酸', '高尿酸血症', 'CHRONIC', '内分泌代谢', 9),
 ('体重指数', '肥胖', 'CHRONIC', '内分泌代谢', 10),
 ('腹部B超', '脂肪肝', 'CHRONIC', '消化系统', 11),
 ('心电图', '冠心病', 'CHRONIC', '心血管系统', 12),
 ('甲胎蛋白', '恶性肿瘤(疑似)', 'MAJOR', '肿瘤', 13),
 ('癌胚抗原', '恶性肿瘤(疑似)', 'MAJOR', '肿瘤', 14),
 ('头颅CT', '脑卒中', 'MAJOR', '神经系统', 15)
ON DUPLICATE KEY UPDATE disease_name = VALUES(disease_name);
