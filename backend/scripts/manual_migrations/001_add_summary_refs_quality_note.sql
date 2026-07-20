-- backend/scripts/manual_migrations/001_add_summary_refs_quality_note.sql
ALTER TABLE report_interpretation
  ADD COLUMN summary_refs JSON NULL AFTER summary_text,
  ADD COLUMN quality_note VARCHAR(255) NULL AFTER summary_refs;
