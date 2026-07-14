"""term_normalizer 纯函数单测：名称标准化 + 同名同值指标去重。无 DB 依赖。"""
from app.core.term_normalizer import normalize_indicators, normalize_item_name


def test_normalize_item_name_alias_to_standard():
    assert normalize_item_name("血糖")[0] == "空腹血糖（GLU）"
    assert normalize_item_name("葡萄糖")[0] == "空腹血糖（GLU）"
    assert normalize_item_name("谷丙转氨酶")[0] == "丙氨酸氨基转移酶（ALT）"
    # 未知名称原样保留
    assert normalize_item_name("淋巴细胞百分数")[0] == "淋巴细胞百分数"


def test_normalize_indicators_sets_standard_and_code():
    out = normalize_indicators([{"item_name": "血糖", "result": "6.8"}])
    assert out[0]["item_name_standard"] == "空腹血糖（GLU）"
    assert out[0]["item_code"] is None


def test_dedup_same_name_same_value():
    """同名同值的多条应合并为一条（主检/科普/分项在一次 PDF 里重复出现）"""
    indicators = [
        {"item_name": "淋巴细胞百分数", "result": "51.00", "unit": "%"},
        {"item_name": "中性粒细胞百分数", "result": "37.70", "unit": "%"},
        {"item_name": "淋巴细胞百分数", "result": "51.00", "unit": "%"},  # 重复
        {"item_name": "血清丙氨酸氨基转移酶", "result": "66.00", "unit": "U/L"},
        {"item_name": "中性粒细胞百分数", "result": "37.70", "unit": "%"},  # 重复
    ]
    out = normalize_indicators(indicators)
    names = [i["item_name"] for i in out]
    assert names == ["淋巴细胞百分数", "中性粒细胞百分数", "血清丙氨酸氨基转移酶"]
    # 第一条的 unit 等保留
    assert out[0]["unit"] == "%"


def test_dedup_same_name_different_value_kept():
    """同名不同值视作不同指标（如收缩压/舒张压不同时间点的不同值），不能合并"""
    indicators = [
        {"item_name": "尿酸", "result": "431"},
        {"item_name": "尿酸", "result": "414"},
        {"item_name": "尿酸", "result": "431"},  # 与第一条相同，应合并
    ]
    out = normalize_indicators(indicators)
    results = [i["result"] for i in out]
    assert results == ["431", "414"]


def test_dedup_by_standard_name():
    """原名不同但标准化后相同且值相同的，应合并（如"血糖"和"葡萄糖"两次同值）"""
    indicators = [
        {"item_name": "血糖", "result": "6.8"},
        {"item_name": "葡萄糖", "result": "6.8"},  # 同标准名同值
        {"item_name": "血糖", "result": "5.5"},  # 同标准名不同值
    ]
    out = normalize_indicators(indicators)
    results = [i["result"] for i in out]
    assert results == ["6.8", "5.5"]


def test_dedup_empty_result_same_name_kept_once():
    """空 result 的同名指标应合并，避免空值也被记录多次"""
    indicators = [
        {"item_name": "尿胆原", "result": None, "unit": None},
        {"item_name": "尿胆原", "result": None, "unit": None},
    ]
    out = normalize_indicators(indicators)
    assert len(out) == 1


def test_dedup_preserves_order():
    """去重应保留首次出现位置，整体顺序不变"""
    indicators = [
        {"item_name": "A", "result": "1"},
        {"item_name": "B", "result": "2"},
        {"item_name": "A", "result": "1"},  # 重复
        {"item_name": "C", "result": "3"},
    ]
    out = normalize_indicators(indicators)
    assert [(i["item_name"], i["result"]) for i in out] == [("A", "1"), ("B", "2"), ("C", "3")]


def test_dedup_no_change_when_no_duplicates():
    """无重复时返回长度、内容不变"""
    indicators = [
        {"item_name": "尿酸", "result": "431"},
        {"item_name": "肌酸激酶", "result": "294"},
    ]
    out = normalize_indicators(indicators)
    assert len(out) == 2