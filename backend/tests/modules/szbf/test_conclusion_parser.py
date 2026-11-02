from app.modules.szbf.conclusion_parser import _is_useful_title, parse_con_suggest
from app.modules.szbf.profiles import SzbfProfile, get_profile


def _names(result, kind=None):
    return [t.name for t in result.titles if kind is None or t.kind == kind]


def test_numbered_multi_findings_and_suggestions():
    text = (
        "1.①心电图提示：窦性心动过缓（59次/分）；②心脏彩超提示：三尖瓣反流(轻度）、"
        "二尖瓣反流（轻度）\n"
        "建议：请进行年度体检复查，如有不适随时心血管内科就诊。\n"
        "2.CT提示：左肺下叶微小钙化灶\n"
        "建议：请呼吸科年度体检复查。\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    names = _names(result)
    assert "窦性心动过缓" in names
    assert "三尖瓣反流" in names
    assert "二尖瓣反流" in names
    assert "左肺下叶微小钙化灶" in names
    assert not any("建议" in n for n in names)


def test_legacy_title_format():
    text = (
        "幽门螺杆菌阳性：幽门螺杆菌是一种微需氧菌，是慢性胃炎的主要致病因素。\n"
        "鼻中隔弯曲：先天性异常或是后天鼻外伤引起的后遗症。\n"
        "窦性心律不齐：通常为生理性的心动周期随呼吸、情绪、运动发生变化。"
    )
    result = parse_con_suggest(text, get_profile("133"))
    names = _names(result)
    assert "幽门螺杆菌阳性" in names
    assert "鼻中隔弯曲" in names
    assert "窦性心律不齐" in names


def test_bracket_titles_and_numeric_brackets_ignored():
    text = "【尿酸偏高】建议复查\n【脂肪肝】\n【1】逆钟向转位可见于正常人"
    result = parse_con_suggest(text, get_profile("133"))
    names = _names(result)
    assert "尿酸偏高" in names
    assert "脂肪肝" in names
    assert "1" not in names


def test_indicator_lines_are_supplements():
    text = (
        "1.*尿酸偏高\n"
        "*尿酸:493μmol/L↑(120-420)\n"
        "甘油三酯:2.94mmol/L↑(<1.70)\n"
        "尿白细胞：+-\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    by_name = {i.item_name: i for i in result.indicators}
    assert by_name["尿酸"].result_value == "493"
    assert by_name["尿酸"].unit == "μmol/L"
    assert by_name["尿酸"].deviation == "偏高"
    assert by_name["甘油三酯"].deviation == "偏高"
    assert by_name["尿白细胞"].result_value == "+-"
    assert any(t.name == "尿酸偏高" and t.kind == "indicator" for t in result.titles)


def test_normal_and_measurement_lines_ignored():
    text = (
        "1.甲状腺未见明显异常\n"
        "2.心脏彩超：LVEF68%\n"
        "3.前列腺增生伴钙化\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    names = _names(result)
    assert "甲状腺未见明显异常" not in names
    assert "LVEF68%" not in names
    assert "前列腺增生伴钙化" in names


def test_bracket_advice_does_not_duplicate_title():
    text = "【尿酸偏高】建议复查\n【1】逆钟向转位可见于正常人"
    result = parse_con_suggest(text, get_profile("133"))
    names = _names(result)
    assert names == ["尿酸偏高"]
    assert not any("建议" in name for name in names)
    assert [i.item_name for i in result.indicators] == ["尿酸"]


def test_laterality_is_preserved():
    text = "1.甲状腺结节（左）\n2.甲状腺结节（右）"
    result = parse_con_suggest(text, get_profile("133"))
    names = _names(result)
    assert "甲状腺结节（左）" in names
    assert "甲状腺结节（右）" in names
    assert "甲状腺结节" not in names


def test_severity_and_heart_rate_annotations_stripped():
    text = "1.三尖瓣反流（轻度）\n2.窦性心动过缓（59次/分）"
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == ["三尖瓣反流", "窦性心动过缓"]


def test_size_and_classification_annotations_preserved():
    text = "1.甲状腺结节（TI-RADS 3类）\n2.前列腺增生伴钙化（约4.2×3.1cm）"
    result = parse_con_suggest(text, get_profile("133"))
    names = _names(result)
    assert "甲状腺结节（TI-RADS 3类）" in names
    assert "前列腺增生伴钙化（约4.2×3.1cm）" in names


def test_compound_findings_split_only_when_each_part_is_complete():
    text = (
        "1.双肺纹理增多、紊乱\n"
        "2.肝内钙化灶、囊肿\n"
        "3.谷丙转氨酶、谷草转氨酶偏高\n"
        "4.三尖瓣反流(轻度）、二尖瓣反流（轻度）\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    names = _names(result)
    assert "双肺纹理增多、紊乱" in names
    assert "紊乱" not in names
    assert "肝内钙化灶、囊肿" in names
    assert "囊肿" not in names
    assert "谷丙转氨酶、谷草转氨酶偏高" in names
    assert "谷丙转氨酶" not in names
    assert "三尖瓣反流" in names
    assert "二尖瓣反流" in names


def test_method_prefixed_compound_findings_split():
    text = "1.心电图提示：窦性心动过缓、心脏彩超提示：三尖瓣反流(轻度）"
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == ["窦性心动过缓", "三尖瓣反流"]


def test_semiquantitative_results_are_qualitative():
    result = parse_con_suggest("尿蛋白:1+\n尿糖:2+", get_profile("133"))
    by_name = {i.item_name: i for i in result.indicators}
    assert by_name["尿蛋白"].result_value == "1+"
    assert by_name["尿蛋白"].unit is None
    assert by_name["尿蛋白"].deviation == "异常"
    assert by_name["尿糖"].result_value == "2+"
    assert by_name["尿糖"].unit is None
    assert by_name["尿糖"].deviation == "异常"
    assert _names(result) == []


def test_lab_unit_with_text_flag_is_split():
    result = parse_con_suggest("尿酸:493μmol/L偏高", get_profile("133"))
    indicator = result.indicators[0]
    assert indicator.result_value == "493"
    assert indicator.unit == "μmol/L"
    assert indicator.deviation == "偏高"

    result = parse_con_suggest("尿酸:493(偏高)", get_profile("133"))
    indicator = result.indicators[0]
    assert indicator.result_value == "493"
    assert indicator.unit is None
    assert indicator.deviation == "偏高"


def test_lab_lines_do_not_produce_finding_titles():
    text = "*尿酸:493μmol/L↑(120-420)\n甘油三酯:2.94mmol/L↑(<1.70)\n尿白细胞：+-"
    result = parse_con_suggest(text, get_profile("133"))
    assert {i.item_name for i in result.indicators} == {"尿酸", "甘油三酯", "尿白细胞"}
    assert _names(result) == []


def test_profile_method_prefixes_and_extra_skip_are_used():
    profile = SzbfProfile(
        org_id="test",
        method_prefixes=("磁共振",),
        extra_skip=("忽略",),
    )
    result = parse_con_suggest("1.磁共振提示：脂肪肝\n2.忽略此条：肝囊肿", profile)
    assert _names(result) == ["脂肪肝"]


def test_section_labels_do_not_become_titles():
    text = (
        "医学解释：高尿酸血症是指血尿酸水平升高。\n"
        "常见原因：尿酸生成过多或排泄减少。\n"
        "健康指导：低嘌呤饮食，多饮水。\n"
        "既往史：糖尿病\n"
        "【医学解释】尿酸是嘌呤代谢产物。\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == []
    assert result.indicators == []


def test_long_advice_lines_do_not_become_titles():
    text = "1.高尿酸血症：建议低嘌呤饮食，多饮水，定期复查尿酸。"
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == []


def test_numbered_lab_segment_is_indicator_not_title():
    text = "1.④检验提示:总胆固醇:5.21(mmol/L)↑(参考值:＜5.18)"
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == []
    by_name = {i.item_name: i for i in result.indicators}
    assert by_name["总胆固醇"].result_value == "5.21"
    assert by_name["总胆固醇"].unit == "mmol/L"
    assert by_name["总胆固醇"].deviation == "偏高"


def test_blood_pressure_lines_are_ignored():
    assert _names(parse_con_suggest("血压：138/96mmHg", get_profile("133"))) == []
    assert parse_con_suggest("血压：138/96mmHg", get_profile("133")).indicators == []
    assert _names(parse_con_suggest("1.血压：138/96mmHg)", get_profile("133"))) == []


def test_blood_pressure_is_not_an_indicator_supplement():
    result = parse_con_suggest("血压:138", get_profile("133"))
    assert _names(result) == []
    assert result.indicators == []


def test_numbered_pure_measurement_is_ignored():
    result = parse_con_suggest("1.4.2×3.1cm", get_profile("133"))
    assert _names(result) == []


def test_numeric_bracket_with_colon_does_not_leak_via_legacy():
    result = parse_con_suggest("【1】逆钟向转位：可见于正常人", get_profile("133"))
    assert _names(result) == []


def test_slash_units_are_kept_for_indicators():
    result = parse_con_suggest("尿白细胞:5/HP↑", get_profile("133"))
    assert _names(result) == []
    by_name = {i.item_name: i for i in result.indicators}
    assert by_name["尿白细胞"].result_value == "5"
    assert by_name["尿白细胞"].unit == "/HP"


def test_numbered_lab_with_slash_unit_is_indicator_not_title():
    result = parse_con_suggest("1.白细胞:10/μL↑", get_profile("133"))
    assert _names(result) == []
    by_name = {i.item_name: i for i in result.indicators}
    assert by_name["白细胞"].result_value == "10"
    assert by_name["白细胞"].unit == "/μL"


def test_long_advice_without_suggestion_word_does_not_become_title():
    text = "1.高尿酸血症：应注意低嘌呤饮食，多饮水，定期复查尿酸。"
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == []


def test_enumerated_section_labels_do_not_become_titles():
    text = (
        "1.【医学解释】尿酸是嘌呤代谢产物。\n"
        "一、医学解释：尿酸生成过多或排泄减少。\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == []
    assert result.indicators == []


def test_long_finding_with_reaction_word_is_kept():
    text = "1.双侧颈部多发淋巴结反应性增生（较大者约1.2cm）"
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == ["双侧颈部多发淋巴结反应性增生（较大者约1.2cm）"]


def test_junk_indicator_titles_are_filtered():
    text = (
        "1.胃蛋白酶原I检测异常，I/II比值正常\n"
        "2.您本次体检肌酸激酶升高。肌酸激酶升高常见原因包括\n"
        "3.乙肝表面抗体轻度升高\n"
        "4.乙肝表面抗体升高+核心抗体\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    assert not any(t.kind == "indicator" for t in result.titles)
    assert result.indicators == []


def test_severity_fragment_is_not_an_indicator():
    result = parse_con_suggest("1.乙肝表面抗体轻度", get_profile("133"))
    assert result.indicators == []
    assert not any(t.kind == "indicator" for t in result.titles)


def test_numeric_lab_supplement_requires_abnormality_marker():
    result = parse_con_suggest("维生素D:20", get_profile("133"))
    assert result.indicators == []

    result = parse_con_suggest("*尿酸:493μmol/L↑(120-420)", get_profile("133"))
    by_name = {i.item_name: i for i in result.indicators}
    assert by_name["尿酸"].result_value == "493"
    assert by_name["尿酸"].unit == "μmol/L"
    assert by_name["尿酸"].deviation == "偏高"


def test_junk_lab_indicator_names_are_filtered():
    text = (
        "血: +-\n"
        "4: 3↑\n"
        "a): +-\n"
        "(3)血: +-\n"
        "II比值: 2.31↑\n"
        "17检测: 8.31↑\n"
        "常见生理性增高原因: 1↑\n"
        "白细胞分类: +-\n"
        "C13检测室: 阳性\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    assert result.indicators == []
    assert _names(result) == []


def test_junk_indicator_title_names_are_filtered():
    text = (
        "1.白细胞分类异常\n"
        "2.肌酸激酶增高常见生理性原因\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    assert not any(t.kind == "indicator" for t in result.titles)
    assert result.indicators == []


def test_known_latin_indicator_names_are_kept():
    result = parse_con_suggest("PSA: 4.2↑\nHBsAb: 120↑", get_profile("133"))
    assert {i.item_name for i in result.indicators} == {"PSA", "HBsAb"}


def test_paren_enumeration_is_supported():
    result = parse_con_suggest("1）脂肪肝\n2)肝囊肿", get_profile("133"))
    assert _names(result) == ["脂肪肝", "肝囊肿"]


def test_numeric_prefix_titles_are_not_stripped():
    result = parse_con_suggest(
        "1.25羟基维生素D不足\n2.13碳尿素呼气试验阳性", get_profile("133")
    )
    names = _names(result)
    assert "25羟基维生素D不足" in names
    assert "13碳尿素呼气试验阳性" in names
    assert "25羟基维生素D" in {i.item_name for i in result.indicators}


def test_year_prefixed_section_label_is_not_a_title():
    result = parse_con_suggest("2026年体检结论：脂肪肝", get_profile("133"))
    assert _names(result) == []


def test_method_or_department_only_titles_are_rejected():
    profile = get_profile("133")
    for text in (
        "超声提示", "CT提示", "心脏彩超提示", "外科", "检查结论",
        "眼底右", "耳鼻喉科查体提示", "胸部CT提示：", "(1)超声提示：",
        "超声心动图提示", "口腔科",
    ):
        assert not _is_useful_title(text, profile), text


def test_method_only_legacy_heads_do_not_leak():
    text = (
        "超声提示：\n"
        "心脏彩超提示：\n"
        "外科：\n"
        "检查结论：\n"
        "眼底右：\n"
    )
    result = parse_con_suggest(text, get_profile("133"))
    assert _names(result) == []


def test_tianjin_280_comma_list():
    profile = get_profile("280")
    text = (
        "牙周炎,骨密度测值低于同龄人骨密度（Z≤-2.0）,低密度脂蛋白胆固醇偏低,"
        "尿隐血2+,彩超提示甲状腺呈弥漫性改变,磷(P)偏低,"
    )
    result = parse_con_suggest(text, profile)
    names = [t.name for t in result.titles]
    assert "牙周炎" in names
    assert "甲状腺呈弥漫性改变" in names
    assert "彩超提示甲状腺呈弥漫性改变" not in names
    indicator_names = {i.item_name for i in result.indicators}
    assert "尿隐血" in indicator_names
    assert "磷(P)" in indicator_names


def test_tianjin_280_preserves_fullwidth_comma():
    result = parse_con_suggest(
        "彩超提示双侧椎动脉管径不对称，左侧细", get_profile("280")
    )
    assert [t.name for t in result.titles] == ["双侧椎动脉管径不对称，左侧细"]
