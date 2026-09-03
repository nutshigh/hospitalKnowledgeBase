from app.core.term_normalizer import normalize_item_name, normalize_indicators


def test_glucose_disambiguated_blood_vs_urine():
    assert normalize_item_name("葡萄糖", result="5.16", unit="mmol/L")[0] == "空腹血糖"
    assert normalize_item_name("葡萄糖", result="阴性", unit="mmol/L")[0] == "尿葡萄糖(GLU)"
    assert normalize_item_name("葡萄糖", result="-", unit="")[0] == "尿葡萄糖(GLU)"


def test_wbc_disambiguated_blood_vs_urine():
    assert normalize_item_name("白细胞", result="5.53", unit="10~9/L")[0] == "白细胞数(WBC)"
    assert normalize_item_name("白细胞", result="阴性", unit="个/HP")[0] == "尿白细胞(LEU)"
    assert normalize_item_name("白细胞", result="5", unit="个/视野")[0] == "白细胞"


def test_rbc_disambiguated_blood_vs_urine():
    assert normalize_item_name("红细胞", result="4.66", unit="10~12/L")[0] == "红细胞数(RBC)"
    assert normalize_item_name("红细胞", result="5", unit="/μL")[0] == "尿红细胞(镜检)"
    assert normalize_item_name("红细胞", result="3", unit="个")[0] == "红细胞"


def test_bilirubin_not_eaten_by_short_alias():
    assert normalize_item_name("总胆红素")[0] == "总胆红素(TBIL)"
    assert normalize_item_name("胆红素", result="阴性")[0] == "尿胆红素(BIL)"
    assert normalize_item_name("直接胆红素")[0] == "直接胆红素(DBIL)"


def test_bmi_unified():
    assert normalize_item_name("体重指数")[0] == "体重指数"
    assert normalize_item_name("体质指数")[0] == "体重指数"


def test_conclusion_normalization():
    assert normalize_item_name("甲状腺双叶多发囊性结节")[0] == "甲状腺囊性结节"
    assert normalize_item_name("右肺尖间隔旁型肺气肿")[0] == "肺气肿（间隔旁型）"


def test_high_prevalence_conclusion_normalization():
    assert normalize_item_name("中度脂肪肝")[0] == "脂肪肝"
    assert normalize_item_name("脂肪肝(重度)")[0] == "脂肪肝"
    assert normalize_item_name("脂肪浸润")[0] == "脂肪肝"
    assert normalize_item_name("颈动脉内膜增厚伴斑块形成")[0] == "颈动脉粥样硬化"
    assert normalize_item_name("颈动脉斑块")[0] == "颈动脉粥样硬化"
    assert normalize_item_name("骨量减少")[0] == "骨质疏松"
    assert normalize_item_name("幽门螺杆菌阳性")[0] == "幽门螺杆菌感染"
    assert normalize_item_name("幽门螺旋杆菌抗体阳性")[0] == "幽门螺杆菌感染"
    assert normalize_item_name("前列腺肥大")[0] == "前列腺增生"
    assert normalize_item_name("乳腺小叶增生")[0] == "乳腺增生"
    assert normalize_item_name("子宫平滑肌瘤")[0] == "子宫肌瘤"
    assert normalize_item_name("胆结石")[0] == "胆囊结石"
    assert normalize_item_name("甲状腺实性结节")[0] == "甲状腺结节"
    assert normalize_item_name("甲状腺右叶混合性结节")[0] == "甲状腺结节"
    assert normalize_item_name("甲状腺左叶结节")[0] == "甲状腺结节"
    assert normalize_item_name("乙肝表面抗原阳性")[0] == "乙肝表面抗原(HBsAg)"


def test_cystic_node_not_generalized_to_thryoid_node():
    """囊性结节不被"甲状腺结节"别名吞掉(子串顺序保护)。"""
    assert normalize_item_name("甲状腺囊性结节")[0] == "甲状腺囊性结节"
    # 裸"囊性结节"不做别名: 防"乳腺囊性结节"跨器官误指为甲状腺
    assert normalize_item_name("甲状腺多发囊性结节")[0] == "甲状腺多发囊性结节"


def test_urea_before_urea_nitrogen():
    assert normalize_item_name("尿素氮")[0] == "尿素(BUN)"
    assert normalize_item_name("尿素")[0] == "尿素(BUN)"


def test_normalize_indicators_uses_context():
    inds = normalize_indicators([
        {"item_name": "葡萄糖", "result": "5.16", "unit": "mmol/L"},
        {"item_name": "葡萄糖", "result": "阴性", "unit": "mmol/L"},
    ])
    stds = [i["item_name_standard"] for i in inds]
    assert "空腹血糖" in stds
    assert "尿葡萄糖(GLU)" in stds
