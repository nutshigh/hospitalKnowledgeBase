-- 清空「近期健康变化」缓存(JSON)与退役的旧双报告对比纯文本。
-- 属可再生缓存:下次访问 /profile/change-overview 会自动重算写回,无数据损失。
-- 2026-09-10:入选规则/排序/条数改为与指标走势一致,旧缓存口径已过时,需一次性清理。
UPDATE report_interpretation SET comparison_summary = NULL WHERE comparison_summary IS NOT NULL;
