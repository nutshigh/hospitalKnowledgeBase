"""中央标准映射种子(CENTRAL source)。人工审核后的权威清单在此维护。

同步函数幂等:按 item_name_standard / rule_code upsert,不删除已有行。
单指标: (item_name_standard, disease_name, category, class, match_level, match_deviation, sort_code)
组合: (rule_code, disease_name, category, class, member_items, sort_code)
match_level: YELLOW(黄及以上命中) / RED(仅红命中); match_deviation: 偏高/偏低/None(不约束)
"""
from sqlalchemy import text

CENTRAL_MAPPINGS = [
    # ---- CHRONIC 心血管 ----
    ("收缩压(高压)", "高血压", "CHRONIC", "心血管系统", "YELLOW", "偏高", 1),
    ("舒张压(低压)", "高血压", "CHRONIC", "心血管系统", "YELLOW", "偏高", 2),
    ("同型半胱氨酸", "高同型半胱氨酸血症", "CHRONIC", "心血管系统", "YELLOW", "偏高", 3),
    ("室上性早搏", "心律失常", "CHRONIC", "心血管系统", "YELLOW", None, 4),
    ("窦性心律不齐", "心律失常", "CHRONIC", "心血管系统", "YELLOW", None, 5),
    # ---- CHRONIC 内分泌代谢 ----
    ("空腹血糖", "糖尿病", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 6),
    ("糖化血红蛋白", "糖尿病", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 7),
    ("总胆固醇(CHOL)", "血脂异常", "CHRONIC", "心血管系统", "YELLOW", "偏高", 8),
    ("甘油三酯(TG)", "血脂异常", "CHRONIC", "心血管系统", "YELLOW", "偏高", 9),
    ("低密度脂蛋白胆固醇(LDL)", "血脂异常", "CHRONIC", "心血管系统", "YELLOW", "偏高", 10),
    ("高密度脂蛋白胆固醇(HDL)", "血脂异常", "CHRONIC", "心血管系统", "YELLOW", "偏低", 11),
    ("小而密低密度脂蛋白胆固醇", "血脂异常", "CHRONIC", "心血管系统", "YELLOW", "偏高", 12),
    ("尿酸(UA)", "高尿酸血症", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 13),
    ("总甲状腺原氨酸(T4)", "甲状腺功能亢进", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 14),
    ("总三碘甲状腺原氨酸(T3)", "甲状腺功能亢进", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 15),
    ("游离三碘甲状腺原氨酸(FT3)", "甲状腺功能亢进", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 16),
    ("游离甲状腺素(FT4)", "甲状腺功能亢进", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 17),
    ("促甲状腺激素(TSH)", "甲状腺功能减退", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 18),
    # ---- CHRONIC 其他/消化/泌尿 ----
    # 囊性结节与实性/混合性结节性质不同, 只映射到囊性本身, 不泛化为"甲状腺结节"(有歧义)。
    # 2026-08-19: 囊性结节为超声检查发现(良性胶质囊肿), 非疾病实体, 归 OTHER(统计白名单已剔除)。
    ("甲状腺囊性结节", "甲状腺囊性结节", "OTHER", "其他", "YELLOW", None, 19),
    # 贫血收敛(2026-08-19): 仅 Hb 为核心指标直接命中;
    # MCV/MCH/MCHC/RDW 等不再单独命中, 避免地贫携带/缺铁早期等 Hb 正常者误记贫血。
    ("血红蛋白(HGB)", "贫血", "CHRONIC", "其他", "YELLOW", "偏低", 20),
    ("肌酐", "慢性肾病", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 27),
    ("尿素(BUN)", "慢性肾病", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 28),
    ("尿蛋白(PRO)", "慢性肾病", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 29),
    ("尿潜血(BLD)", "慢性肾病", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 30),
    ("尿微量蛋白(UMA)", "慢性肾病", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 31),
    ("尿白细胞(LEU)", "尿路感染", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 32),
    ("亚硝酸盐(NIT)", "尿路感染", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 33),
    ("丙氨酸氨基转移酶(谷丙酶)", "肝功能异常", "CHRONIC", "消化系统", "YELLOW", "偏高", 34),
    ("天门冬氨酸氨基转移酶(谷草酶)", "肝功能异常", "CHRONIC", "消化系统", "YELLOW", "偏高", 35),
    ("谷氨酰转移酶(r-GT)", "肝功能异常", "CHRONIC", "消化系统", "YELLOW", "偏高", 36),
    ("总胆红素(TBIL)", "肝功能异常", "CHRONIC", "消化系统", "YELLOW", "偏高", 37),
    ("直接胆红素(DBIL)", "肝功能异常", "CHRONIC", "消化系统", "YELLOW", "偏高", 38),
    ("间接胆红素(IBIL)", "肝功能异常", "CHRONIC", "消化系统", "YELLOW", "偏高", 39),
    ("总胆汁酸", "肝功能异常", "CHRONIC", "消化系统", "YELLOW", "偏高", 40),
    ("胆囊多发结节", "胆囊息肉样病变", "CHRONIC", "消化系统", "YELLOW", None, 41),
    # 2026-08-19: 胆固醇结晶为超声检查发现(胆石症前驱征象), 非疾病实体, 归 OTHER。
    ("胆囊壁胆固醇结晶", "胆囊壁胆固醇结晶", "OTHER", "消化系统", "YELLOW", None, 42),
    # 口腔常见小问题(2026-08-19): 龋齿/扁桃体肥大/屈光不正/外耳道耵聍 非慢性病, 归 OTHER,
    # 保留映射仅作解读链接; 统计层另有白名单剔除(见 disease_service)。
    # 牙周病属 WHO 慢性非传染性疾病: 牙龈炎/牙结石为其病因/早期表现, 保留 CHRONIC 计入统计。
    ("龋齿", "龋齿", "OTHER", "其他", "YELLOW", None, 43),
    ("牙龈炎", "牙周病", "CHRONIC", "其他", "YELLOW", None, 44),
    ("牙结石", "牙周病", "CHRONIC", "其他", "YELLOW", None, 45),
    ("扁桃体肥大", "扁桃体肥大", "OTHER", "其他", "YELLOW", None, 46),
    ("屈光不正", "屈光不正", "OTHER", "其他", "YELLOW", None, 47),
    ("外耳道耵聍", "外耳道耵聍", "OTHER", "其他", "YELLOW", None, 48),
    # ---- CHRONIC 内分泌代谢(2026-08-19): 肥胖为 WHO 认定的慢性疾病 ----
    # 保持 RED 命中: 黄区(超重)不计入肥胖, 避免超重检出率(>50%)刷屏统计口径。
    ("体重指数", "肥胖症", "CHRONIC", "内分泌代谢", "RED", "偏高", 49),
    ("肝内钙化灶", "肝内钙化灶", "OTHER", "消化系统", "YELLOW", None, 50),
    # ---- MAJOR 肿瘤 ----
    # 肿瘤标志物收紧(2026-08-19): 单指标 RED 才命中, 降低假阳性(CA125/CA199/CEA 等良性病亦升高)。
    # 组合命中可承载低可靠性线索(见 CENTRAL_RULES 说明), 需新增时以 combo 规则表达。
    ("甲胎蛋白(AFP)定量", "肝癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 60),
    ("癌胚抗原(CEA)定量", "结直肠癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 61),
    ("CA125", "卵巢癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 62),
    ("CA153", "乳腺癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 63),
    ("CA-199", "胰腺癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 64),
    ("神经元特异性烯醇化酶", "肺癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 65),
    ("肿瘤特异生长因子", "恶性肿瘤(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 66),
    ("总前列腺特异性抗原(TPSA)", "前列腺癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 67),
    ("游离前列腺特异性抗原(FPSA)", "前列腺癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 68),
    ("尿核基质蛋白(NMP22)测定", "膀胱癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 69),
    # 结论/检查类(非标志物)保持 YELLOW: 影像/专项检查结论本身即可靠证据
    ("肺结节", "肺癌(疑似)", "MAJOR", "肿瘤", "YELLOW", None, 70),
    # ---- MAJOR 心血管 ----
    ("肌酸激酶(CK)", "心肌损伤(疑似)", "MAJOR", "心血管系统", "YELLOW", "偏高", 71),
    ("乳酸脱氢酶(LDH)", "心肌损伤(疑似)", "MAJOR", "心血管系统", "YELLOW", "偏高", 72),
    ("α-羟丁酸脱氢酶", "心肌损伤(疑似)", "MAJOR", "心血管系统", "YELLOW", "偏高", 73),
    ("心肌肌钙蛋白I", "心肌梗死(疑似)", "MAJOR", "心血管系统", "RED", "偏高", 74),
    ("肌酸激酶MB型同工酶(CK-MB)", "心肌梗死(疑似)", "MAJOR", "心血管系统", "RED", "偏高", 75),
    # ---- MAJOR 肿瘤补充(2026-08-18, 标准表补齐; 2026-08-19 标志物统一收紧为 RED) ----
    ("CA724", "胃癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 76),
    ("鳞状细胞癌相关抗原 (SCC)", "鳞状细胞癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 77),
    ("细胞角蛋白19片段", "肺癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 78),
    ("EB病毒抗体", "鼻咽癌(疑似)", "MAJOR", "肿瘤", "RED", "偏高", 79),
    ("膀胱癌尿FISH测定", "膀胱癌(疑似)", "MAJOR", "肿瘤", "YELLOW", None, 80),
    # ---- CHRONIC 心血管/血脂补充(2026-08-18) ----
    ("血清载脂蛋白A", "血脂异常", "CHRONIC", "心血管系统", "YELLOW", "偏低", 81),
    ("血清载脂蛋白B", "血脂异常", "CHRONIC", "心血管系统", "YELLOW", "偏高", 82),
    ("D-二聚体", "血栓风险", "CHRONIC", "心血管系统", "YELLOW", "偏高", 83),
    ("利钠肽(BNP)", "心力衰竭(疑似)", "CHRONIC", "心血管系统", "YELLOW", "偏高", 84),
    ("神经末端利钠肽原(NT-ProBNP)", "心力衰竭(疑似)", "CHRONIC", "心血管系统", "YELLOW", "偏高", 85),
    ("超敏肌钙蛋白(hs-cTn)", "心肌梗死(疑似)", "MAJOR", "心血管系统", "RED", "偏高", 86),
    # ---- CHRONIC 泌尿补充(2026-08-18) ----
    ("胱抑素C", "慢性肾病", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 87),
    ("尿蛋白/尿肌酐比值", "慢性肾病", "CHRONIC", "泌尿系统", "YELLOW", "偏高", 88),
    # ---- CHRONIC 内分泌补充(2026-08-18, 甲状腺自身抗体) ----
    ("抗过氧化物酶抗体(TR-Ab)", "桥本甲状腺炎(疑似)", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 89),
    ("抗球蛋白抗体(TG)", "桥本甲状腺炎(疑似)", "CHRONIC", "内分泌代谢", "YELLOW", "偏高", 90),
    # ---- CHRONIC 高检出率慢病补充(2026-08-19, 体检必查项/检查结论来源) ----
    # 输入来源均为彩超/呼气试验/骨密度等明确检查结论(总检建议), 结论型条目走精确+子串匹配。
    # 2026-08-19 类别声明: 慢性病/重大疾病映射目标=疾病实体; 超声/影像"检查发现"
    # (甲状腺结节/乳腺增生/卵巢囊肿等)归 OTHER, 作为非指标项异常发现供前端 OTHER 筛选, 非疾病实体。
    ("脂肪肝", "脂肪肝", "CHRONIC", "消化系统", "YELLOW", None, 91),
    ("胆囊结石", "胆囊结石", "CHRONIC", "消化系统", "YELLOW", None, 92),
    ("肾结石", "肾结石", "CHRONIC", "泌尿系统", "YELLOW", None, 93),
    ("幽门螺杆菌感染", "幽门螺杆菌感染", "CHRONIC", "消化系统", "YELLOW", None, 94),
    ("颈动脉粥样硬化", "颈动脉粥样硬化", "CHRONIC", "心血管系统", "YELLOW", None, 95),
    ("骨质疏松", "骨质疏松", "CHRONIC", "其他", "YELLOW", None, 96),
    ("前列腺增生", "前列腺增生", "CHRONIC", "其他", "YELLOW", None, 97),
    ("子宫肌瘤", "子宫肌瘤", "CHRONIC", "其他", "YELLOW", None, 99),
    # 检查发现类(OTHER, 供前端 OTHER 筛选): 甲状腺结节(良性 95%, 需随访) /
    # 乳腺增生(影像描述) / 卵巢囊肿(多数生理性)。实性/混合性结节归一化别名负责短语。
    ("乳腺增生", "乳腺增生", "OTHER", "其他", "YELLOW", None, 98),
    ("卵巢囊肿", "卵巢囊肿", "OTHER", "其他", "YELLOW", None, 100),
    ("甲状腺结节", "甲状腺结节", "OTHER", "其他", "YELLOW", None, 101),
    # 乙肝两对半 HBsAg 阳性 → 携带/慢性乙肝(定性指标, 黄/红判定即阳性, 不约束方向)
    ("乙肝表面抗原(HBsAg)", "乙肝病毒携带", "CHRONIC", "其他", "YELLOW", None, 102),
]

CENTRAL_RULES = [
    ("C-HT", "高血压", "CHRONIC", "心血管系统", [
        {"name": "收缩压(高压)", "min_level": "YELLOW", "deviation": "偏高"},
        {"name": "舒张压(低压)", "min_level": "YELLOW", "deviation": "偏高"},
    ], 1),
    ("C-DM", "糖尿病", "CHRONIC", "内分泌代谢", [
        {"name": "空腹血糖", "min_level": "YELLOW", "deviation": "偏高"},
        {"name": "糖化血红蛋白", "min_level": "YELLOW", "deviation": "偏高"},
    ], 2),
    ("C-LIPID", "血脂异常", "CHRONIC", "心血管系统", [
        {"name": "总胆固醇(CHOL)", "min_level": "YELLOW", "deviation": "偏高"},
        {"name": "低密度脂蛋白胆固醇(LDL)", "min_level": "YELLOW", "deviation": "偏高"},
    ], 3),
    ("C-MET", "代谢综合征", "CHRONIC", "内分泌代谢", [
        {"name": "体重指数", "min_level": "YELLOW", "deviation": "偏高"},
        {"name": "甘油三酯(TG)", "min_level": "YELLOW", "deviation": "偏高"},
        {"name": "空腹血糖", "min_level": "YELLOW", "deviation": "偏高"},
    ], 4),
    ("C-AMI", "急性心肌梗死(疑似)", "MAJOR", "心血管系统", [
        {"name": "心肌肌钙蛋白I", "min_level": "RED", "deviation": "偏高"},
        {"name": "肌酸激酶(CK)", "min_level": "YELLOW", "deviation": "偏高"},
    ], 5),
    ("C-HYPERTH", "甲状腺功能亢进", "CHRONIC", "内分泌代谢", [
        {"name": "促甲状腺激素(TSH)", "min_level": "YELLOW", "deviation": "偏低"},
        {"name": "游离甲状腺素(FT4)", "min_level": "YELLOW", "deviation": "偏高"},
    ], 6),
]


def sync_central(db):
    """把 CENTRAL 数据 upsert 进该 tenant 库(保留已有 LOCAL 行)。幂等。"""
    import json as _json

    for std, dname, cat, klass, level, dev, sort in CENTRAL_MAPPINGS:
        row = db.execute(text(
            "SELECT id FROM disease_mapping WHERE item_name_standard=:s"
        ), {"s": std}).fetchone()
        if row:
            db.execute(text(
                "UPDATE disease_mapping SET disease_name=:d, disease_category=:c,"
                " disease_class=:k, sort_code=:o, source='CENTRAL',"
                " match_level=:lv, match_deviation=:dev WHERE id=:id"
            ), {"d": dname, "c": cat, "k": klass, "o": sort, "lv": level,
                "dev": dev, "id": row.id})
        else:
            db.execute(text(
                "INSERT INTO disease_mapping (item_name_standard, disease_name,"
                " disease_category, disease_class, sort_code, source,"
                " match_level, match_deviation)"
                " VALUES (:s, :d, :c, :k, :o, 'CENTRAL', :lv, :dev)"
            ), {"s": std, "d": dname, "c": cat, "k": klass, "o": sort,
                "lv": level, "dev": dev})
    for code, dname, cat, klass, members, sort in CENTRAL_RULES:
        existing = db.execute(text(
            "SELECT id FROM disease_rule WHERE rule_code=:c"
        ), {"c": code}).fetchone()
        if existing:
            db.execute(text(
                "UPDATE disease_rule SET disease_name=:d, disease_category=:c,"
                " disease_class=:k, member_items=:mi, sort_code=:o, source='CENTRAL'"
                " WHERE id=:id"
            ), {"d": dname, "c": cat, "k": klass,
                "mi": _json.dumps(members, ensure_ascii=False), "o": sort,
                "id": existing.id})
        else:
            db.execute(text(
                "INSERT INTO disease_rule (rule_code, disease_name, disease_category,"
                " disease_class, member_items, sort_code, source)"
                " VALUES (:rc, :d, :c, :k, :mi, :o, 'CENTRAL')"
            ), {"rc": code, "d": dname, "c": cat, "k": klass,
                "mi": _json.dumps(members, ensure_ascii=False), "o": sort})
    # 中央清单收缩/改名时停用旧 CENTRAL 条目(不删除, 保留解读链路历史引用)。
    # 例: 贫血收敛后 MCV/MCH/RDW 等不再单独命中(2026-08-19)。
    _names = [m[0] for m in CENTRAL_MAPPINGS]
    _phs = ",".join(f":n{i}" for i in range(len(_names)))
    db.execute(text(
        f"UPDATE disease_mapping SET enabled=0 WHERE enabled=1 AND source='CENTRAL'"
        f" AND item_name_standard NOT IN ({_phs})"
    ), {f"n{i}": n for i, n in enumerate(_names)})
    db.commit()
