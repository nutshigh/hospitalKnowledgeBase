-- Add columns to cache AI-generated comparison summary between reports
ALTER TABLE report_interpretation
  ADD COLUMN comparison_summary TEXT NULL AFTER summary_refs,
  ADD COLUMN comparison_baseline_id BIGINT NULL AFTER comparison_summary;