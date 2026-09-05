from app.core.indicator_groups import normalize_panel, group_indicators


def test_normalize_panel_valid_and_whitespace():
    assert normalize_panel("尿常规") == "尿常规"
    assert normalize_panel("  尿常规 ") == "尿常规"
    assert normalize_panel("尿 常规") == "尿常规"   # 内部空格(含全角空格场景)
    assert normalize_panel(None) is None
    assert normalize_panel("") is None
    assert normalize_panel("   ") is None
    assert normalize_panel("乱写") is None
    assert normalize_panel("血常规,糖化血红蛋白") is None  # 非白名单整串不进


def test_group_stored_category_wins_over_name():
    rows, order = group_indicators([
        {"item_name": "葡萄糖", "category": "尿常规"},
        {"item_name": "白细胞", "category": "尿常规"},
        {"item_name": "全血糖化血红蛋白测定", "category": "糖化血红蛋白"},
    ])
    by = {r["item_name"]: r["group"] for r in rows}
    assert by["葡萄糖"] == "尿常规"                 # 名字会归 空腹血糖
    assert by["白细胞"] == "尿常规"                 # 名字会归 血常规
    assert by["全血糖化血红蛋白测定"] == "糖化血红蛋白"  # 名字会归 糖化血红蛋白(但保真)
    assert order == ["尿常规", "糖化血红蛋白"]


def test_group_invalid_category_falls_back_to_name():
    rows, _ = group_indicators([
        {"item_name": "葡萄糖", "category": "乱写"},
        {"item_name": "葡萄糖", "category": "   "},
        {"item_name": "白细胞", "category": "NMP22测定"},
    ])
    assert [r["group"] for r in rows] == ["空腹血糖", "空腹血糖", "血常规"]


def test_group_without_category_unchanged():
    rows, _ = group_indicators([{"item_name": "血红蛋白"}, {"item_name": "尿酸碱度"}])
    assert [r["group"] for r in rows] == ["血常规", "尿常规"]
