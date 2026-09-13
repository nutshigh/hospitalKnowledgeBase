from app.modules.risk.engine import compute_hits


def make_j(name, level, iid, deviation=None):
    class J:
        def __init__(self):
            self.item_name = name
            self.color_level = level
            self.indicator_id = iid
            self.deviation = deviation
    return J()


STD = {
    1: "肌酸激酶（CK）",
    2: "低密度脂蛋白胆固醇（LDL-C）",
    3: "收缩压（SBP）",
    4: "舒张压（DBP）",
    5: "空腹血糖（GLU）",
    6: "超氧化物歧化酶（SOD）",
    7: "体质指数（BMI）",
}


def test_single_hit_matches_standard_name():
    mappings = [{"id": 1, "item_name_standard": "肌酸激酶（CK）", "disease_name": "心肌损伤(疑似)",
                 "disease_category": "MAJOR", "disease_class": "心血管系统",
                 "match_level": "YELLOW", "match_deviation": "偏高"}]
    judgments = [make_j("血肌酸激酶", "yellow", 1, "偏高")]
    hits = compute_hits(judgments, STD, mappings, [])
    assert hits == [{"disease_name": "心肌损伤(疑似)", "disease_category": "MAJOR",
                     "disease_class": "心血管系统", "hit_type": "single",
                     "mapping_id": 1, "rule_id": None, "hit_items": ["肌酸激酶（CK）"]}]


def test_deviation_wrong_direction_rejected():
    mappings = [{"id": 1, "item_name_standard": "收缩压（SBP）", "disease_name": "高血压",
                 "disease_category": "CHRONIC", "disease_class": "心血管系统",
                 "match_level": "YELLOW", "match_deviation": "偏高"}]
    j_low = [make_j("收缩压", "yellow", 3, "偏低")]
    assert compute_hits(j_low, STD, mappings, []) == []
    j_high = [make_j("收缩压", "yellow", 3, "偏高")]
    assert len(compute_hits(j_high, STD, mappings, [])) == 1


def test_deviation_high_low_normalized():
    mappings = [{"id": 1, "item_name_standard": "肌酸激酶（CK）", "disease_name": "心肌损伤(疑似)",
                 "disease_category": "MAJOR", "disease_class": "心血管系统",
                 "match_level": "YELLOW", "match_deviation": "偏高"}]
    j = [make_j("肌酸激酶", "yellow", 1, "high")]
    assert len(compute_hits(j, STD, mappings, [])) == 1


def test_level_red_required():
    mappings = [{"id": 1, "item_name_standard": "体质指数（BMI）", "disease_name": "肥胖症",
                 "disease_category": "OTHER", "disease_class": "其他",
                 "match_level": "RED", "match_deviation": "偏高"}]
    j_yellow = [make_j("体质指数", "yellow", 7, "偏高")]
    assert compute_hits(j_yellow, STD, mappings, []) == []
    j_red = [make_j("体质指数", "red", 7, "偏高")]
    assert len(compute_hits(j_red, STD, mappings, [])) == 1


def test_strictest_wins_same_indicator():
    mappings = [
        {"id": 1, "item_name_standard": "体质指数（BMI）", "disease_name": "超重",
         "disease_category": "OTHER", "disease_class": "其他",
         "match_level": "YELLOW", "match_deviation": "偏高"},
        {"id": 2, "item_name_standard": "体质指数（BMI）", "disease_name": "肥胖症",
         "disease_category": "OTHER", "disease_class": "其他",
         "match_level": "RED", "match_deviation": "偏高"},
    ]
    j_red = [make_j("体质指数", "red", 7, "偏高")]
    hits = compute_hits(j_red, STD, mappings, [])
    assert [h["disease_name"] for h in hits] == ["肥胖症"]
    j_yellow = [make_j("体质指数", "yellow", 7, "偏高")]
    hits = compute_hits(j_yellow, STD, mappings, [])
    assert [h["disease_name"] for h in hits] == ["超重"]


def test_combo_requires_all_members():
    rules = [{"id": 10, "rule_code": "C-HT", "disease_name": "高血压",
              "disease_category": "CHRONIC", "disease_class": "心血管系统",
              "member_items": [
                  {"name": "收缩压（SBP）", "min_level": "YELLOW", "deviation": "偏高"},
                  {"name": "舒张压（DBP）", "min_level": "YELLOW", "deviation": "偏高"},
              ]}]
    j1 = [make_j("收缩压", "yellow", 3, "偏高")]
    assert compute_hits(j1, STD, [], rules) == []
    j2 = j1 + [make_j("舒张压", "red", 4, "偏高")]
    hits = compute_hits(j2, STD, [], rules)
    assert hits == [{"disease_name": "高血压", "disease_category": "CHRONIC",
                     "disease_class": "心血管系统", "hit_type": "combo",
                     "mapping_id": None, "rule_id": 10,
                     "hit_items": ["收缩压（SBP）", "舒张压（DBP）"]}]


def test_combo_member_wrong_direction_rejected():
    rules = [{"id": 10, "rule_code": "C-HT", "disease_name": "高血压",
              "disease_category": "CHRONIC", "disease_class": "心血管系统",
              "member_items": [
                  {"name": "收缩压（SBP）", "min_level": "YELLOW", "deviation": "偏高"},
                  {"name": "舒张压（DBP）", "min_level": "YELLOW", "deviation": "偏高"},
              ]}]
    j = [make_j("收缩压", "yellow", 3, "偏高"),
         make_j("舒张压", "yellow", 4, "偏低")]
    assert compute_hits(j, STD, [], rules) == []


def test_green_ignored():
    mappings = [{"id": 2, "item_name_standard": "低密度脂蛋白胆固醇（LDL-C）", "disease_name": "血脂异常",
                 "disease_category": "CHRONIC", "disease_class": "心血管系统",
                 "match_level": "YELLOW", "match_deviation": "偏高"}]
    judgments = [make_j("低密度脂蛋白胆固醇", "green", 2, "偏高")]
    assert compute_hits(judgments, STD, mappings, []) == []


def test_same_disease_merged_combo_wins():
    mappings = [{"id": 3, "item_name_standard": "空腹血糖（GLU）", "disease_name": "糖尿病",
                 "disease_category": "CHRONIC", "disease_class": "内分泌代谢",
                 "match_level": "YELLOW", "match_deviation": "偏高"}]
    rules = [{"id": 11, "rule_code": "C-DM", "disease_name": "糖尿病",
              "disease_category": "CHRONIC", "disease_class": "内分泌代谢",
              "member_items": [
                  {"name": "空腹血糖（GLU）", "min_level": "YELLOW", "deviation": "偏高"},
                  {"name": "超氧化物歧化酶（SOD）", "min_level": "YELLOW", "deviation": None},
              ]}]
    j = [make_j("空腹血糖", "yellow", 5, "偏高"),
         make_j("超氧化物歧化酶", "yellow", 6, None)]
    hits = compute_hits(j, STD, mappings, rules)
    assert len(hits) == 1
    assert hits[0]["hit_type"] == "combo"
    assert set(hits[0]["hit_items"]) == {"空腹血糖（GLU）", "超氧化物歧化酶（SOD）"}


def make_conclusion_j(name, level, deviation=None):
    class JC:
        def __init__(self):
            self.item_name = name
            self.color_level = level
            self.indicator_id = None
            self.deviation = deviation
            self.source = "conclusion"
    return JC()


def test_conclusion_substring_hit():
    """结论型自由文本("脂肪肝(中度)")含标准名子串 → 命中。"""
    mappings = [{"id": 20, "item_name_standard": "脂肪肝", "disease_name": "脂肪肝",
                 "disease_category": "CHRONIC", "disease_class": "消化系统",
                 "match_level": "YELLOW", "match_deviation": None}]
    j = [make_conclusion_j("脂肪肝(中度)", "yellow")]
    hits = compute_hits(j, {}, mappings, [])
    assert [h["disease_name"] for h in hits] == ["脂肪肝"]


def test_conclusion_substring_reverse_hit():
    """结论名被标准名包含(标准名"颈动脉粥样硬化", 结论名更短的情形) → 命中。"""
    mappings = [{"id": 21, "item_name_standard": "颈动脉粥样硬化", "disease_name": "颈动脉粥样硬化",
                 "disease_category": "CHRONIC", "disease_class": "心血管系统",
                 "match_level": "YELLOW", "match_deviation": None}]
    j = [make_conclusion_j("颈动脉粥样硬化斑块", "yellow")]
    hits = compute_hits(j, {}, mappings, [])
    assert len(hits) == 1


def test_short_std_no_substring():
    """2 字标准名(如"尿糖")不做子串匹配, 防"尿糖阳性"类误命中短名。"""
    mappings = [{"id": 22, "item_name_standard": "尿糖", "disease_name": "糖尿病",
                 "disease_category": "CHRONIC", "disease_class": "内分泌代谢",
                 "match_level": "YELLOW", "match_deviation": None}]
    j = [make_conclusion_j("尿糖阳性", "yellow")]
    assert compute_hits(j, {}, mappings, []) == []


def test_indicator_no_substring():
    """生化/指标型条目(无 conclusion source)不做子串匹配(仅精确)。"""
    mappings = [{"id": 23, "item_name_standard": "低密度脂蛋白胆固醇(LDL)", "disease_name": "血脂异常",
                 "disease_category": "CHRONIC", "disease_class": "心血管系统",
                 "match_level": "YELLOW", "match_deviation": "偏高"}]
    j = [make_j("低密度脂蛋白", "yellow", None, "偏高")]
    assert compute_hits(j, {}, mappings, []) == []


def test_conclusion_green_ignored():
    mappings = [{"id": 24, "item_name_standard": "脂肪肝", "disease_name": "脂肪肝",
                 "disease_category": "CHRONIC", "disease_class": "消化系统",
                 "match_level": "YELLOW", "match_deviation": None}]
    j = [make_conclusion_j("脂肪肝(中度)", "green")]
    assert compute_hits(j, {}, mappings, []) == []
