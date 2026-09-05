import json
from pathlib import Path

from app.core.indicator_groups import MODULE_ORDER

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "excel_row2_modules.json"


def test_module_order_matches_excel_row2_snapshot():
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert MODULE_ORDER == snapshot
    assert len(snapshot) == 70
