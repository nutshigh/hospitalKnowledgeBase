"""影像检查 11 项纯函数断言(秒级, 无 LLM / 无 DB)。

用例来源: docs/superpowers/specs/2026-10-14-imaging-items-module-design.md
测试与验证节 + 77 份存量语料探针实测案例。
运行: cd backend && .venv/bin/python -m pytest tests/modules/report/test_imaging_items.py -q
"""
from app.modules.report.imaging_items import build_imaging_items, collect_evidence


def _levels(items):
    return {i["name"]: i["level"] for i in items}


def _one(name, level, text="", gender=None, entries=None):
    lv = _levels(build_imaging_items(text, gender, entries))
    assert lv[name] == level, f"{name}: 期望 {level}, 实得 {lv}"


# === 正常句 (P1) ===

def test_normal_statements_green():
    _one("心电图", "green", "2.正常心电图")
    _one("彩超(心脏)", "green", "心脏彩超：房室大小及LVEF值正常范围")
    _one("心电图", "green", "1、窦性心律2、正常范围心电图")
    _one("彩超(心脏)", "green", "2.静息状态下左室整体收缩功能正常")
    _one("心电图", "yellow", "窦性心律不齐")          # 非正常节律仍判黄
    _one("心电图", "yellow", "窦性心动过缓")


# === 检查表头 (P2) ===

def test_exam_headers_skipped():
    _one("彩超(心脏)", "green", "【心脏彩超】")
    _one("心电图", "green", "二、心电图室:")
    _one("心电图", "green", "四、【常规心电图检查费】")
    # 003-26 真实表头(带字间空格): 表头跳过, 下一行发现仍命中
    _one("胸部CT", "green", "7 、【 腰 椎 L 3-S1 / 胸 部 低 剂量 螺旋C T 平扫 】")
    _one("胸部CT", "yellow",
         "7 、【 腰 椎 L 3-S1 / 胸 部 低 剂量 螺旋C T 平扫 】\n"
         "1 、两 肺 微 小实性 结 节 ， 较前片（2026 - 0 6 -03 ）大致相 仿 。")
    # 反例保护: 【】内文含检查词但不是结尾 → 发现行
    _one("彩超(心脏)", "yellow", "1、彩超-二、三尖瓣少量反流")
    _one("彩超(甲状腺)", "yellow", "5、彩超-双侧甲状腺囊性结节（C-TIRADS 2类，六类法）")


# === 科普解释段 (P3) ===

def test_science_prose_not_flagged():
    _one("头颅CT/核磁", "green",
         "血脂检查提示：总胆固醇(5.63mmol/L)、低密度脂蛋白（4.09mmol/L）偏高；"
         "载脂蛋白B（1.32g/L）偏高。血脂异常是脑梗死、脑出血、冠心病、心肌梗死等"
         "心脑血管疾病及胰腺炎危险因素。")
    _one("头颅CT/核磁", "green", "避免劳累和用脑过度")
    _one("胸部CT", "green",
         "右心室肥厚（慢性阻塞性肺疾病、肺栓塞、肺纤维化、肺动脉高压等；心脏结构问题如"
         "房间隔缺损、法洛四联症、肺动脉瓣狭窄等先天性心脏病），左后分支传导阻滞，"
         "侧壁心肌梗死，右位心等。")
    _one("彩超(腹部+女泌尿系)", "green",
         "血肌酐主要源于体内肌肉组织代谢的产物，能较准确反应肾脏受损的情况，但并非敏感"
         "指标。在肾功能损害早期，血肌酐可在正常范围；当肾小球滤过率下降到正常的50%以下时"
         "才迅速升高，常同时伴随血尿素氮升高。")
    # 003-28 科普: 【】行只取首句, "血栓脱落进入颅内血管" 次句不参与
    _one("头颅CT/核磁", "green",
         "【颈动脉斑块形成】颈动脉斑块形成是最常见的血管疾病，其病理变化主要是动脉内膜"
         "类脂质沉积，使内膜增厚，进一步发展到斑块或血栓形成，导致管腔狭窄。血栓脱落进入"
         "颅内血管引起脑栓塞。超声所见符合颈动脉斑块形成诊断，请听从专科医生解释。")
    # 心脑血管疾病/脑血管病 泛称不当脑部影像
    _one("头颅CT/核磁", "green", "(1)混合型高脂血症指胆固醇及甘油三酯均有升高。是心脑血管疾病发生的重要危险因素。")
    _one("头颅CT/核磁", "green", "可导致心脑血管疾病、糖尿病等慢性病发生。")
    # TCD(经颅多普勒) 非头颅CT/核磁
    _one("头颅CT/核磁", "green", "★颅内多普勒血流图(TCD）:基底动脉轻度狭窄")
    # 化验科普行(肌酸激酶/血脂等)
    _one("彩超(甲状腺)", "green",
         "血清肌酸激酶(CPK)是一种器官特异酶，主要存在于心肌、甲状腺、骨骼肌和脑组织中。")
    _one("彩超(腹部+男泌尿系)", "green",
         "性过敏反应等。其增高可见于慢性溶血、脾切除、铅及铋中毒及慢性粒细胞白血病等。建议您血液科门诊进一步复查诊治。")
    # 建议提及检查部位(如胸部CT或胸片)不当作发现
    _one("胸部X片", "green", "指导治疗。如胸部CT或胸片发现肺结节，建议到肺结节门诊或呼吸内科进一步诊治。")


# === 化验衍生 (P3) ===

def test_lab_derived_not_flagged():
    _one("彩超(腹部+男泌尿系)", "yellow", "肝功能异常；肝囊肿")   # 遮蔽后保留肝囊肿
    _one("彩超(腹部+男泌尿系)", "green", "(1)肌酐(CREA)偏高(114μmol/L)隔期复查，肾病学科诊治。")
    _one("彩超(甲状腺)", "green", "甲状腺功能中部分指标检测异常")
    _one("彩超(腹部+男泌尿系)", "green",
         "1.血尿酸（UA）偏高，临床上称为\"高尿酸血症\"（HUA）。长期HUA会引起痛风、"
         "痛风石、尿酸性肾病，也是心血管疾病的危险因素之一。")
    # 乙型肝炎(非"乙肝"字面)表面抗体行不命中肝
    _one("彩超(腹部+男泌尿系)", "green", "12:乙型肝炎表面抗体、核心抗体阳性")
    # 手术史提及甲状腺不当甲状腺彩超异常
    _one("彩超(甲状腺)", "green", "手术史：甲状腺手术")
    # 弃检/未检/显示不清
    _one("彩超(腹部+男泌尿系)", "green", "(2)前列腺放弃")
    _one("彩超(腹部+男泌尿系)", "green", "腹腔胀气明显，胰腺显示不清")
    # 必要时泌尿外科诊治(建议提及部位)
    _one("彩超(腹部+男泌尿系)", "green", "2、可能为尿路感染，必要时泌尿外科诊治。")


# === 长句截断 ===

def test_truncate_at_marker():
    _one("彩超(心脏)", "yellow",
         "8.二尖瓣、三尖瓣轻度反流多属于生理性，不需特殊处理，建议心血管内科随诊。")
    _one("彩超(腹部+男泌尿系)", "yellow", "4. 彩超提示肝囊肿：")
    _one("彩超(甲状腺)", "yellow", "10、甲状腺右叶结节（C-TIRADS 2）：甲状腺结节是常见的"
                                     "甲状腺疾病，3-6个月左右复查超声，如有变化普外科就诊。")
    # 例外: "监测动态心电图" 不作截断点
    _one("心电图", "yellow", "2.监测动态心电图1.窦性心律2.偶发房性早搏3.偶发多源室性早搏")
    # 例外: "检查提示：" 是发现引导语, 不作截断点(H004-79 式)
    _one("彩超(心脏)", "yellow", "本次体检彩超室检查提示：三尖瓣少量反流。")
    _one("彩超(颈动脉)", "yellow", "本次检查发现【双侧颈动脉内-中膜毛糙，血流通畅D12】")
    # 建议行开头 → 不判黄
    _one("彩超(心脏)", "green", "（3）定期复查心电图、心脏彩超检查，如有胸闷、胸痛，建议进一步检冠脉相关检查。")


# === 头部 / 颈动脉 / 肺瓣 防误报 ===

def test_keyword_false_positive_guards():
    _one("头颅CT/核磁", "yellow", "腔隙性脑梗死")
    _one("彩超(颈动脉)", "green", "胆囊内膜欠光滑")
    _one("彩超(颈动脉)", "yellow", "双侧颈动脉内-中膜增厚")
    items = _levels(build_imaging_items("肺动脉瓣少量反流", None))
    assert items["彩超(心脏)"] == "yellow"
    assert items["胸部CT"] == "green"


# === 源A 条目 + 并集 ===

def test_entries_source_and_union():
    items = _levels(build_imaging_items("", None, [("室间隔基底段增厚", "yellow")]))
    assert items["彩超(心脏)"] == "yellow"
    items = _levels(build_imaging_items(None, "女", [("甲状腺右侧叶实性结节", "yellow")]))
    assert items["彩超(甲状腺)"] == "yellow"
    # 条目名内部空白折叠(T 波改变)
    items = _levels(build_imaging_items("", None, [("T 波改变", "yellow")]))
    assert items["心电图"] == "yellow"
    # 并集: 条目红 + 原文黄 → 红
    items = _levels(build_imaging_items("1.甲状腺结节", None, [("甲状腺结节", "red")]))
    assert items["彩超(甲状腺)"] == "red"
    # 条目正常词过滤
    items = _levels(build_imaging_items("", None, [("未见明显异常", "yellow")]))
    assert items["彩超(心脏)"] == "green"


# === 红守卫 ===

def test_red_guard():
    text = "6.甲状腺右侧叶实性结节\n如有胸闷、胸痛、心悸，请及时就诊。"
    assert _levels(build_imaging_items(text, None))["彩超(甲状腺)"] == "yellow"
    text = "6.甲状腺右侧叶实性结节\n建议尽快就诊。"
    assert _levels(build_imaging_items(text, None))["彩超(甲状腺)"] == "red"
    # 无影像命中的块不升红
    text = "6.尿酸升高\n建议尽快就诊。"
    assert _levels(build_imaging_items(text, None))["心电图"] == "green"
    # 长条件句(若…请及时复诊)不升红 (H004-5 式)
    text = ("6.左肾结石\n若复查病灶持续增大或突发严重腰痛、肉眼血尿、腹痛等症状，请及时复诊。")
    assert _levels(build_imaging_items(text, None))["彩超(腹部+男泌尿系)"] == "yellow"


# === 用户审表反馈回归(2026-10-14, 7 例) ===

def test_user_review_feedback_cases():
    # 黄佳军: 冒号标题行内含发现 → 不跳, ECG 判黄
    _one("心电图", "yellow", "* 心电图提示J点上抬:\n若有心慌、胸闷、胸痛等不适症状，建议心内科就诊。")
    # 生明月: "双乳结节" 命中乳腺
    _one("彩超(乳腺)", "yellow", "* 双乳结节(BI-RADS 3 类)。:")
    # 董涛: 裸"肺"(非肺动脉/肺功能) 命中胸部CT
    _one("胸部CT", "yellow", "11、肺增殖灶\n多考虑为肺部既往感染愈合后组织修复遗留的良性陈旧性瘢痕病灶。")
    # 方轶涛: P波改变 命中 ECG
    _one("心电图", "yellow", "P波改变\n建议结合自身症状，心血管内科随访诊治。")
    # 朱品龙: 左心室高电压 是心电图征象, 不命中彩超(心脏); 仍命中 ECG
    items = _levels(build_imaging_items("6:心电图左心室高电压心室早期复极T波改变", None))
    assert items["心电图"] == "yellow"
    assert items["彩超(心脏)"] == "green"
    items = _levels(build_imaging_items("", None, [("左心室高电压", "yellow")]))
    assert items["心电图"] == "yellow"
    assert items["彩超(心脏)"] == "green"
    # 曹敬金: 独立椎动脉彩超不算颈动脉彩超
    _one("彩超(颈动脉)", "green", "13、【椎动脉彩超】\n双侧椎动脉内径不对称（左侧为优势）")
    items = _levels(build_imaging_items(
        "12、【颈动脉彩超】\n双侧颈动脉未见明显异常\n13、【椎动脉彩超】\n双侧椎动脉内径不对称（左侧为优势）", None))
    assert items["彩超(颈动脉)"] == "green"
    # 徐伟祥: 颈部淋巴结不是颈动脉
    _one("彩超(颈动脉)", "green", "● 颈部淋巴结？\nB超提示右侧颈部Ⅱ、Ⅲ区可见数个椭圆形低回声团，考虑淋巴结可能性大。")
    # 肺动脉瓣/肺功能 仍不归胸部CT
    _one("胸部CT", "green", "肺动脉瓣少量反流")
    _one("胸部CT", "green", "轻度阻塞性肺通气功能障碍")
    # 耳穴贴豆穴位列表含"肺"字 → 不归胸部CT (蔡镇宇)
    _one("胸部CT", "green",
         "(5)耳穴贴豆：可选口、胃、脾、肺、三焦、饥点、内分泌、皮质下等穴，自行按压穴位2-3分钟，每天3次。")


# === 性别 / 空文本 ===

def test_gender_filter_and_empty():
    assert len(build_imaging_items("甲状腺结节", "男")) == 8
    assert len(build_imaging_items("甲状腺结节", "女")) == 10
    assert len(build_imaging_items("甲状腺结节", None)) == 11
    assert "彩超(子宫附件)" not in _levels(build_imaging_items("子宫肌瘤", "男"))
    assert "彩超(腹部+男泌尿系)" not in _levels(build_imaging_items("肝囊肿", "女"))
    assert build_imaging_items("", None, None) == []
    assert build_imaging_items(None, "男", []) == []


# === 审表证据 ===

def test_collect_evidence_tags():
    ev = collect_evidence("6.甲状腺结节", [("甲状腺结节", "yellow")])
    assert any(x.startswith("[条目]") for x in ev["彩超(甲状腺)"])
    assert any(x.startswith("[原文]") for x in ev["彩超(甲状腺)"])


def test_imaging_false_positive_filters_for_new_batch():
    # 化验解释续行/肾内科解释/妇科查体字段不产生影像假黄
    _one("胸部CT", "green",
         "1.明显偏高多见于心肌梗死、风湿性心肌炎、多发性\n"
         "皮肌炎、甲状腺功能减退、脑血管意外、脑膜炎、药物肌肉注射、中风惊厥、瘫痪等。")
    _one("彩超(腹部+男泌尿系)", "green",
         "1、肾脏受损、尿毒症期、慢性肾衰竭代偿、甲状腺功能亢进等。2、请您及时到肾内科诊治。")
    _one("彩超(子宫附件)", "green",
         "宫颈:表面光滑\n子宫位置:\n附件：未检")
    _one("彩超(子宫附件)", "green", "21、宫颈防癌液基薄层细胞制片术：炎症反应性细胞改变")


def test_timely_visit_and_importance_are_red_triggers():
    # 用户 2026-10-21 口径: "及时就诊"/"重视"可作为红区触发词
    text = "【1】左侧额叶缺血灶\n建议您结合既往病史及临床症状到神经科就诊，请您重视，及时就诊，定期复查。"
    assert _levels(build_imaging_items(text, None))["头颅CT/核磁"] == "red"
    text2 = "【1】急性脑梗死\n建议立即就医。"
    assert _levels(build_imaging_items(text2, None))["头颅CT/核磁"] == "red"
