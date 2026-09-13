"""term_normalizer 纯函数单测：名称标准化 + 同名同值指标去重。无 DB 依赖。"""
from app.core.term_normalizer import (
    normalize_indicators,
    normalize_item_name,
    is_child_item,
)


def test_normalize_item_name_alias_to_standard():
    assert normalize_item_name("血糖")[0] == "空腹血糖"
    assert normalize_item_name("葡萄糖")[0] == "空腹血糖"
    assert normalize_item_name("谷丙转氨酶")[0] == "丙氨酸氨基转移酶(谷丙酶)"
    # 方案2量词收敛(2026-08-24): 百分数/百分率/比率 → 百分比口径标准名
    assert normalize_item_name("淋巴细胞百分数")[0] == "淋巴细胞百分比(LYM)"
    assert normalize_item_name("中性粒细胞数")[0] == "中性粒细胞绝对值(NEUT#)"
    # 方案1清洗(2026-08-24): 全角/空格/前后缀
    assert normalize_item_name("血清钾")[0] == "钾(K)"
    assert normalize_item_name("α－羟丁酸脱氢酶")[0] == "α-羟丁酸脱氢酶"
    # 未知名称原样保留
    assert normalize_item_name("随机未知项目X")[0] == "随机未知项目X"


def test_normalize_indicators_sets_standard_and_code():
    out = normalize_indicators([{"item_name": "血糖", "result": "6.8"}])
    assert out[0]["item_name_standard"] == "空腹血糖"
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


def test_no_substring_swallow_child_into_parent():
    """血常规子项不再被子串吞成父项,各自映射到子项 canonical(feat/app 词表)。"""
    assert normalize_item_name("血小板比积")[0] == "血小板压积(PCT)"
    assert normalize_item_name("血小板平均体积")[0] == "血小板平均容积(MPV)"
    assert normalize_item_name("血小板分布宽度")[0] == "血小板分布宽度(PDW)"
    assert normalize_item_name("大血小板比率")[0] == "大血小板比例(P-LCR)"
    assert normalize_item_name("血小板计数")[0] == "血小板数(PLT)"
    assert normalize_item_name("平均红细胞体积")[0] == "红细胞平均体积(MCV)"
    assert normalize_item_name("平均血红蛋白浓度")[0] == "平均红细胞血红蛋白浓度(MCHC)"
    assert normalize_item_name("小而密低密度脂蛋白胆固醇")[0] == "小而密低密度脂蛋白胆固醇"


def test_no_substring_swallow_urine_or_pH():
    """含父名词干的尿检/酸碱度项不得并入父项。"""
    assert normalize_item_name("尿白细胞酯酶")[0] == "尿白细胞酯酶"
    assert normalize_item_name("尿白细胞（镜检）")[0] == "尿白细胞(镜检)"
    assert normalize_item_name("尿酸碱度")[0] == "尿液酸碱度(PH)"


def test_trailing_english_code_paren_stripped_for_lookup():
    """尾缀英文码括号可剥(base 命中 canonical);中文括号限定语不剥。"""
    assert normalize_item_name("血红蛋白(HGB)")[0] == "血红蛋白(HGB)"
    assert normalize_item_name("血小板计数（PLT）")[0] == "血小板数(PLT)"
    assert normalize_item_name("尿红细胞（镜检）")[0] == "尿红细胞(镜检)"


def test_rdw_canonical_self_resolve():
    """标准名(红细胞分布宽度 RDW-CV/SD)再喂回必须自解析回自身,不得落入通用 RDW。"""
    assert normalize_item_name("红细胞分布宽度（RDW-CV）")[0] == "红细胞变异系数(RDW-CV)"
    assert normalize_item_name("红细胞分布宽度(RDW-CV)")[0] == "红细胞变异系数(RDW-CV)"
    assert normalize_item_name("红细胞分布宽度（RDW-SD）")[0] == "红细胞分布宽度(RDW-SD)"
    assert normalize_item_name("红细胞分布宽度(RDW-SD)")[0] == "红细胞分布宽度(RDW-SD)"


def test_is_child_item_flags():
    assert is_child_item("血小板比积") is True
    assert is_child_item("红细胞压积") is True
    assert is_child_item("小而密低密度脂蛋白胆固醇") is True
    assert is_child_item("血小板计数") is False
    assert is_child_item("尿酸碱度") is False
    assert is_child_item("") is False
