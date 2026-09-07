"""新表须在三处 DDL 源保持一致(AGENTS.md 纪律):01_template_db.sql(平台 2 张)、
start.sh heredoc(租户 3 张)、02_hospital_created.sql(租户 3 张)。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_TABLES = ["followup_template", "followup_template_question"]
TENANT_TABLES = ["followup", "followup_question", "user_notification"]


def test_platform_tables_in_01_template_db():
    txt = (ROOT / "infra/mysql/init/01_template_db.sql").read_text(encoding="utf-8")
    for t in TEMPLATE_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {t}" in txt


def test_tenant_tables_in_startsh_and_proc():
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    proc = (ROOT / "infra/mysql/init/02_hospital_created.sql").read_text(encoding="utf-8")
    for t in TENANT_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {t} " in start
        assert f"@db_name, '`.{t} " in proc


def test_migration_006_covers_all_tenant_dbs():
    mig = (ROOT / "backend/scripts/manual_migrations/006_followup.sql").read_text(encoding="utf-8")
    for hid in ["hospital_H001", "hospital_H002", "hospital_H003", "hospital_H004", "hospital_1"]:
        assert hid in mig
