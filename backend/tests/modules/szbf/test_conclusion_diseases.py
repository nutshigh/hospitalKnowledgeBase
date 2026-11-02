from app.modules.szbf.conclusion_diseases import match_conclusion_diseases


def names(text):
    return [d.name for d in match_conclusion_diseases(text)]


def test_common_findings():
    assert "脂肪肝" in names("B超：脂肪肝")
    assert "前列腺钙化灶" in names("超声提示：前列腺钙化灶")
    assert "甲状腺结节" in names("甲状腺双侧叶多发结节--符合C-TIRADS3类")
    assert "胆囊息肉样病变" in names("胆囊多发息肉")
    assert "肝囊肿" in names("肝多发囊肿")
    assert "颈动脉内中膜增厚" in names("双侧颈动脉内中膜增厚")
    assert "瓣膜反流" in names("心脏彩超：三尖瓣反流（轻度）")


def test_negation_and_suggestion():
    assert names("未见脂肪肝") == []
    assert names("建议到口腔科治疗牙结石") == []


def test_overweight_normalization():
    assert "超重" in names("超重（BMI)")
    assert "超重" in names("肥胖（BMI)")


def test_bmi_terms_alone_are_not_overweight():
    assert "超重" not in names("偏瘦（BMI)")
    assert "超重" not in names("体重指数")
    assert "超重" not in names("体质指数")
    assert "超重" not in names("BMI")


def test_lung_nodule_alias_rule():
    assert "肺结节" in names("双肺多发结节")
    assert "肺结节" in names("左肺下叶微小结节")
    assert "肺结节" not in names("未见肺结节")


def test_tianjin_280_aliases():
    assert "血脂异常" in names("高胆固醇血症")
    assert "血脂异常" in names("高甘油三酯血症")
    assert "甲状腺弥漫性病变" in names("甲状腺呈弥漫性改变")
    assert "前列腺钙化灶" in names("前列腺钙化斑")
    assert "乳腺增生" in names("双乳腺体增生")
    assert "乳腺结节" in names("左乳内上结节样致密")
    assert "窦性心动过缓" in names("窦性心律过缓")
    assert "幽门螺杆菌阳性" in names("呼气试验阳性")
    assert "甲状腺结节" in names("甲状腺双叶结节")
