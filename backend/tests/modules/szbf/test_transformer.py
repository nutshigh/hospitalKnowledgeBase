from datetime import date

from app.modules.szbf.profiles import get_profile
from app.modules.szbf.transformer import SourceExam, _canonical, transform_exam


def _exam():
    return SourceExam(
        user_id="778537160239349760",
        name="周鑫",
        gender="1",
        birthday=date(1991, 12, 6),
        org_id="133",
        org_name="北京总站",
        exam_code="20261029102638",
        exam_date=date(2026, 10, 13),
        details=[
            {"item_name": "体重指数", "result": "26.6", "item_unit": "",
             "reference_range": "18.5-23.99", "exception_flag": "1",
             "exception_tips": "偏高", "item_code": "BMI", "dept_name": "一般检查",
             "is_combine": "0", "delete_flag": "0"},
            {"item_name": "总胆固醇(CHOL)", "result": "5.32", "item_unit": "mmol/L",
             "reference_range": "<5.18", "exception_flag": "1",
             "exception_tips": "偏高", "item_code": "CHOL", "dept_name": "检验科",
             "is_combine": "0", "delete_flag": "0"},
            {"item_name": "身高体重血压", "result": "", "item_unit": "",
             "reference_range": "", "exception_flag": "1",
             "exception_tips": "存在异常", "item_code": "1500", "dept_name": "体检科",
             "is_combine": "1", "delete_flag": "0"},
            {"item_name": "腹部", "result": "脂肪肝", "item_unit": "",
             "reference_range": "", "exception_flag": "1",
             "exception_tips": "", "item_code": "1121", "dept_name": "体检科",
             "is_combine": "0", "delete_flag": "0"},
        ],
        con_suggest=(
            "1.*尿酸偏高\n*尿酸:493μmol/L↑(120-420)\n"
            "2.超声提示：脂肪肝\n建议：低脂饮食。\n"
            "3.甲状腺未见明显异常\n"
            "尿白细胞：+-\n"
        ),
        conclusion=None,
    )


def test_transform_exam():
    target = transform_exam(_exam(), get_profile("133"))
    assert target.user_id.startswith("z") and len(target.user_id) == 16
    assert target.gender == "男"
    assert target.age == 34
    assert target.report_date == date(2026, 10, 13)
    assert target.unit_name == "北京总站"
    names = {i.item_name for i in target.indicators}
    assert {"体重指数", "总胆固醇(CHOL)", "尿酸", "尿白细胞"} <= names
    assert all(i.item_name != "身高体重血压" for i in target.indicators)
    assert all(i.item_name != "腹部" for i in target.indicators)
    assert "超重" in {d.name for d in target.conclusion_diseases}
    assert "脂肪肝" in {d.name for d in target.conclusion_diseases}


def test_indicator_source_con_suggest():
    target = transform_exam(_exam(), get_profile("133"), indicator_source="con_suggest")
    assert all(i.item_name != "总胆固醇(CHOL)" for i in target.indicators)
    assert any(i.item_name == "尿酸" for i in target.indicators)


def _exam_with(con_suggest, details=None):
    return SourceExam(
        user_id="778537160239349760",
        name="周鑫",
        gender="1",
        birthday=date(1991, 12, 6),
        org_id="133",
        org_name="北京总站",
        exam_code="20261029102638",
        exam_date=date(2026, 10, 13),
        details=details or [],
        con_suggest=con_suggest,
        conclusion=None,
    )


def _detail(item_name, result, unit, ref, code):
    return {
        "item_name": item_name, "result": result, "item_unit": unit,
        "reference_range": ref, "exception_flag": "1",
        "exception_tips": "偏高", "item_code": code, "dept_name": "检验科",
        "is_combine": "0", "delete_flag": "0",
    }


def test_merge_deduplicates_canonical_alias_names():
    details = [
        _detail("总胆固醇(CHOL)", "5.32", "mmol/L", "<5.18", "CHOL"),
        _detail("尿酸(UA)", "493", "μmol/L", "120-420", "UA"),
    ]
    target = transform_exam(
        _exam_with(
            "*尿酸:500μmol/L↑(120-420)\n总胆固醇:5.40mmol/L↑(<5.18)",
            details,
        ),
        get_profile("133"),
    )
    chol = [i for i in target.indicators if i.item_name_standard == "总胆固醇(CHOL)"]
    ua = [i for i in target.indicators if i.item_name_standard == "尿酸(UA)"]
    assert len(chol) == 1
    assert chol[0].item_name == "总胆固醇(CHOL)"
    assert len(ua) == 1
    assert ua[0].item_name == "尿酸(UA)"


def test_merge_does_not_supplement_normal_detail_item():
    detail = _detail("总胆固醇(CHOL)", "4.80", "mmol/L", "<5.18", "CHOL")
    detail["exception_flag"] = "0"
    detail["exception_tips"] = ""
    target = transform_exam(
        _exam_with("总胆固醇:5.32mmol/L↑(<5.18)", [detail]),
        get_profile("133"),
    )
    assert all(i.item_name != "总胆固醇(CHOL)" for i in target.indicators)
    assert target.indicators == []


def test_merge_supplements_item_missing_from_detail():
    details = [
        _detail("总胆固醇(CHOL)", "5.32", "mmol/L", "<5.18", "CHOL"),
    ]
    target = transform_exam(
        _exam_with("25-羟维生素D:14ng/mL↓(20-50)", details),
        get_profile("133"),
    )
    names = {i.item_name for i in target.indicators}
    assert "25-羟维生素D" in names


def test_junk_conclusion_titles_do_not_become_indicators():
    con_suggest = (
        "1.胃蛋白酶原I检测异常，I/II比值正常\n"
        "2.您本次体检肌酸激酶升高。肌酸激酶升高常见原因包括\n"
        "3.乙肝表面抗体轻度升高\n"
        "4.乙肝表面抗体升高+核心抗体\n"
    )
    target = transform_exam(_exam_with(con_suggest), get_profile("133"))
    names = {i.item_name for i in target.indicators}
    standards = {i.item_name_standard for i in target.indicators if i.item_name_standard}
    junk = {
        "胃蛋白酶原I检测异常，I/II比值正常",
        "您本次体检肌酸激酶升高。肌酸激酶升高常见原因包括",
        "乙肝表面抗体轻度升高",
        "乙肝表面抗体轻度",
        "乙肝表面抗体升高+核心抗体",
    }
    assert names.isdisjoint(junk)
    assert standards.isdisjoint(junk)
    assert target.indicators == []


def test_canonical_aliases_cover_lab_name_variants():
    cases = {
        "25-羟维生素D": "25羟基维生素D",
        "25-羟基维生素D": "25羟基维生素D",
        "25-羟基维生素D降低": "25羟基维生素D",
        "羟基维生素D": "25羟基维生素D",
        "维生素D": "25羟基维生素D",
        "血清脂蛋白(a)": "脂蛋白(a)",
        "血清脂蛋白Lp(a)": "脂蛋白(a)",
        "脂蛋白Lp(a)": "脂蛋白(a)",
        "胃泌素-17检测": "胃泌素-17",
        "胃泌素17(免疫学方法)": "胃泌素-17",
        "血小板": "血小板数(PLT)",
        "血小板总数": "血小板数(PLT)",
        "淋巴细胞总数": "淋巴细胞绝对值(LYM#)",
        "白细胞总数": "白细胞数(WBC)",
        "中性粒细胞总数": "中性粒细胞绝对值(NEUT#)",
        "单核细胞总数": "单核细胞绝对值(MONO#)",
        "嗜碱性粒细胞总数": "嗜碱性粒细胞绝对值",
        "嗜碱性粒细胞绝对值": "嗜碱性粒细胞绝对值",
        "嗜碱性细胞比例(BASO)": "嗜碱性粒细胞百分数",
        "嗜碱性粒细胞百分数": "嗜碱性粒细胞百分数",
        "天冬氨酸氨基转移酶": "天门冬氨酸氨基转移酶(谷草酶)",
    }
    for raw, expected in cases.items():
        assert _canonical(raw) == expected, raw


def test_unmatched_findings_self_map_to_other():
    target = transform_exam(
        _exam_with("1.宫颈纳囊\n2.P波改变\n3.低骨量"), get_profile("133")
    )
    by_name = {d.name: d for d in target.conclusion_diseases}
    for name in ("宫颈囊肿", "P波改变", "低骨量"):
        assert name in by_name, (name, by_name)
        assert by_name[name].category == "OTHER"
        assert by_name[name].klass == "其他"


def test_matched_finding_is_not_self_mapped_again():
    target = transform_exam(_exam_with("1.脂肪肝"), get_profile("133"))
    assert [d.name for d in target.conclusion_diseases] == ["脂肪肝"]


def test_long_narrative_is_not_self_mapped():
    long_title = "双侧颈部可见多发淋巴结反应性增生部分淋巴结皮质增厚回声明显不均匀"
    target = transform_exam(_exam_with("1." + long_title), get_profile("133"))
    assert all(d.name != long_title for d in target.conclusion_diseases)


def test_negated_or_advice_finding_is_not_self_mapped():
    text = (
        "1.宫颈超薄细胞：未见上皮内病变或恶性病变（NILM）\n"
        "2.甲状腺实质回声欠均匀--请结合实验室检查"
    )
    target = transform_exam(_exam_with(text), get_profile("133"))
    assert target.conclusion_diseases == []


def test_measurement_section_labels_are_not_self_mapped():
    text = "1.体重指数\n2.体重指数（BMI)测量方法\n3.腰臀比"
    target = transform_exam(_exam_with(text), get_profile("133"))
    assert target.conclusion_diseases == []


def test_self_map_strips_method_prefix_with_content():
    text = (
        "1.放射科提示：胸椎侧弯\n"
        "2.超声心动图提示：左房增大\n"
    )
    target = transform_exam(_exam_with(text), get_profile("133"))
    other_names = [d.name for d in target.conclusion_diseases if d.category == "OTHER"]
    assert other_names == ["胸椎侧弯", "左房增大"]


def test_self_map_strips_broader_method_department_prefixes():
    text = "\n".join([
        "1.超声心动图提示左房增大",
        "2.胸部CT：血管壁钙化斑",
        "3.眼科查体：双眼豹纹状眼底",
        "4.妇科超声提示：子宫腺肌症",
        "5.耳鼻喉科查体：慢性鼻窦炎",
        "6.心电图提示异常Q波",
    ])
    target = transform_exam(_exam_with(text), get_profile("133"))
    other_names = [d.name for d in target.conclusion_diseases if d.category == "OTHER"]
    assert other_names == [
        "左房增大", "血管壁钙化斑", "双眼豹纹状眼底",
        "子宫腺肌症", "慢性鼻窦炎", "异常Q波",
    ]


def test_self_map_skips_measurement_method_and_indicator_titles():
    text = "\n".join([
        "1.偏瘦（BMI)",
        "2.体重指数24.6",
        "3.体重指数26.5",
        "4.超声及CT检查提示",
        "5.超声骨密度检测提示",
        "6.ApoA1",
        "7.胃泌素17偏高",
        "8.磷偏低",
        "9.尿酮体",
        "10.妇科TCT（新）",
        "11.本次体检血压149/100mmHg",
        "12.右眼眼压25.7",
        "13.一般眼科检查提示:右眼眼压降低(9.5mmHg)",
    ])
    target = transform_exam(_exam_with(text), get_profile("133"))
    other_names = [d.name for d in target.conclusion_diseases if d.category == "OTHER"]
    assert other_names == []


def test_merge_uses_canonical_aliases_for_con_suggest_variants():
    details = [
        _detail("25-羟维生素D", "14", "ng/mL", "20-50", "VD"),
        _detail("血小板数(PLT)", "210", "10^9/L", "125-350", "PLT"),
        _detail("白细胞数(WBC)", "6.2", "10^9/L", "3.5-9.5", "WBC"),
        _detail("脂蛋白(a)", "300", "mg/L", "<300", "LPA"),
        _detail("胃泌素-17", "3.1", "pmol/L", "1-15", "G17"),
    ]
    con_suggest = (
        "25-羟基维生素D降低:12ng/mL↓(20-50)\n"
        "血小板总数:220 10^9/L↑(125-350)\n"
        "白细胞总数:6.5 10^9/L↑(3.5-9.5)\n"
        "血清脂蛋白Lp(a):320mg/L↑(<300)\n"
        "胃泌素17(免疫学方法):3.5pmol/L↑(1-15)\n"
    )
    target = transform_exam(_exam_with(con_suggest, details), get_profile("133"))
    standards = [i.item_name_standard for i in target.indicators]
    for expected in (
        "25羟基维生素D",
        "血小板数(PLT)",
        "白细胞数(WBC)",
        "脂蛋白(a)",
        "胃泌素-17",
    ):
        assert standards.count(expected) == 1, (expected, standards)
