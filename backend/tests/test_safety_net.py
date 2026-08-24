from app.modules.report.service import _parse_numbered_titles


def test_parse_numbered_titles_basic():
    text = """1、体重指数>24
2、肺结节
4、胆囊结节
8、牙龈炎、牙结石"""
    assert _parse_numbered_titles(text) == ["体重指数>24", "肺结节", "胆囊结节", "牙龈炎、牙结石"]


def test_parse_numbered_titles_ignores_unnumbered():
    text = """1、体重指数>24
体重指数：26.38 (18-24 ) ↑
建议您控制体重。
2、肺结节"""
    assert _parse_numbered_titles(text) == ["体重指数>24", "肺结节"]


def test_parse_numbered_titles_colon_and_punct():
    text = "3、窦性心律不齐:心电图提示。\n5、胆囊壁胆固醇结晶。"
    assert _parse_numbered_titles(text) == ["窦性心律不齐", "胆囊壁胆固醇结晶"]


def test_parse_numbered_titles_empty():
    assert _parse_numbered_titles("") == []
    assert _parse_numbered_titles("无异常") == []
