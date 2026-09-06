from app.core.indicator_groups import classify, group_indicators


def test_classify_blood_routine_members():
    assert classify("白细胞") == "血常规"
    assert classify("血红蛋白(HGB)") == "血常规"
    assert classify("平均红细胞体积") == "血常规"
    assert classify("血小板压积(PCT)") == "血常规"
    assert classify("全血糖化血红蛋白测定") == "糖化血红蛋白"


def test_classify_urine_not_blood_routine():
    assert classify("尿白细胞（镜检）") == "尿常规"
    assert classify("尿白细胞(LEU)阴性") == "尿常规"
    assert classify("红细胞(高倍视野)") == "尿常规"
    assert classify("尿液颜色") == "尿常规"
    assert classify("尿酸碱度") == "尿常规"      # 含"尿酸"子串但不能进肾功能
    assert classify("尿微量白蛋白浓度") == "尿常规"
    assert classify("胆红素") == "尿常规"        # 尿试纸裸项(无总/直接前缀)
    assert classify("酮体") == "尿常规"
    assert classify("蛋白质") == "尿常规"
    assert classify("粘液丝") == "尿常规"
    assert classify("透明管型") == "尿常规"
    assert classify("比重") == "尿常规"
    assert classify("真菌") == "尿常规"


def test_classify_renal_not_urine():
    assert classify("尿酸(UA)") == "肾功能"
    assert classify("尿素") == "肾功能"
    assert classify("血尿酸") == "肾功能"
    assert classify("肌酐(酶法)") == "肾功能"
    assert classify("胱抑素C") == "肾功能"


def test_classify_liver_not_urine():
    assert classify("总胆红素(TBIL)") == "肝功能"
    assert classify("直接胆红素") == "肝功能"
    assert classify("血清间接胆红素(计算值)") == "肝功能"
    assert classify("谷草转氨酶") == "肝功能"
    assert classify("血清天门冬氨酸氨基转移酶") == "肝功能"
    assert classify("白蛋白/球蛋白(A/G)") == "肝功能"
    assert classify("总胆汁酸") == "肝功能"
    assert classify("总胆红质") == "肝功能"
    assert classify("直接胆红质") == "肝功能"
    assert classify("胆碱脂酶") == "肝功能"
    assert classify("丙氨酸氨基转移酶") == "肝功能"
    assert classify("白/球比值(A/G)") == "肝功能"


def test_classify_lipid_and_sugar():
    assert classify("总胆固醇(CHOL)") == "血脂"
    assert classify("甘油三酯") == "血脂"
    assert classify("小而密低密度脂蛋白胆固醇") == "血脂"
    assert classify("载脂蛋白B") == "血脂"
    assert classify("空腹血糖") == "空腹血糖"
    assert classify("葡萄糖") == "空腹血糖"


def test_classify_thyroid_not_surgical():
    assert classify("游离甲状腺素(FT4)测定") == "甲状腺功能"
    assert classify("促甲状腺激素(TSH)测定") == "甲状腺功能"
    assert classify("甲状腺素(T4)") == "甲状腺功能"
    assert classify("游离三碘甲状原氨酸") == "甲状腺功能"


def test_classify_physical_and_ent_oral():
    assert classify("裸眼视力右") == "眼科"
    assert classify("右眼眼压") == "眼科"
    assert classify("扁桃体") == "耳鼻喉科"
    assert classify("鼓膜") == "耳鼻喉科"
    assert classify("牙体") == "口腔科"
    assert classify("龋齿") == "口腔科"
    assert classify("淋巴结") == "外科"
    assert classify("腰臀比") == "身高体重血压"
    assert classify("杂音") == "内科"


def test_classify_tumor_markers_and_others():
    assert classify("甲胎蛋白(AFP)定量") == "甲胎蛋白(AFP)定量"
    assert classify("癌胚抗原(CEA)定量") == "癌胚抗原(CEA)定量"
    assert classify("癌抗原CA19-9") == "CA-199"
    assert classify("癌抗原CA125") == "CA125"
    assert classify("糖原蛋白125") == "CA125"
    assert classify("游离前列腺特异性抗原") == "前列腺特异性抗原"
    assert classify("乙肝表面抗体(HBsAb)") == "乙肝两对半"
    assert classify("血清同型半胱氨酸") == "同型半胱氨酸"
    assert classify("胃部幽门螺杆菌检测") == "胃部幽门螺杆菌检测"
    assert classify("心肌肌钙蛋白I") == "心肌酶谱"
    assert classify("乳酸脱氢酶") == "心肌酶谱"
    assert classify("钾(K)") == "电解质"
    assert classify("钠(Na)") == "电解质"
    assert classify("血沉") == "免疫检测"
    assert classify("C反应蛋白") == "免疫检测"
    assert classify("免疫球蛋白IgG") == "免疫检测"
    assert classify("T淋巴细胞亚群CD3") == "免疫检测"


def test_classify_unmatched_and_normalization():
    assert classify("肿瘤特异生长因子") is None
    assert classify("体检号") is None
    assert classify("咨询电话") is None
    assert classify("    白细胞  ") == "血常规"   # 首尾空白
    assert classify("白细胞 ") == "血常规"        # 尾部半角空格
    assert classify("裸眼视力 右") == "眼科"      # 内部空格(全角/半角混合场景)


def test_group_indicators_order_and_group():
    rows, module_order = group_indicators([
        {"item_name": "甲胎蛋白(AFP)定量"},
        {"item_name": "白细胞"},
        {"item_name": "血红蛋白"},
        {"item_name": "促甲状腺激素(TSH)测定"},
        {"item_name": "肿瘤特异生长因子"},
    ])
    # Excel 顺序:血常规(103) < 甲状腺功能(286) < 甲胎蛋白(AFP)定量(325)
    assert module_order == ["血常规", "甲状腺功能", "甲胎蛋白(AFP)定量"]
    by_name = {r["item_name"]: r["group"] for r in rows}
    assert by_name["白细胞"] == "血常规"
    assert by_name["血红蛋白"] == "血常规"
    assert by_name["促甲状腺激素(TSH)测定"] == "甲状腺功能"
    assert by_name["甲胎蛋白(AFP)定量"] == "甲胎蛋白(AFP)定量"
    assert by_name["肿瘤特异生长因子"] is None
