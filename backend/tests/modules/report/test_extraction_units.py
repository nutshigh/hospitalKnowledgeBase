"""提取链确定性纯函数断言(秒级, 无 LLM / 无 PDF / 无 DB)。

背景(2026-09-09 验证纪律): 全量护栏(结论回归+guard)只覆盖文本定位/切段/标题解析/
判定层; junk 滤卡 / fill 兜底 / _expand_title_segments / 跨线去重(_is_cross_dup)/
_review_block 剥离等"回归盲区"改动**不跑护栏**(跑了也验不出), 由本文件兜底:
直接调用确定性函数, 喂构造输入断言输出。

运行: cd backend && .venv/bin/python -m pytest tests/modules/report/test_extraction_units.py -q
"""
import pytest

from app.modules.report.service import (
    _all_occurrences_in_junk_context,
    _drop_review_block,
    _expand_title_segments,
    _FINDING_TITLE_RE,
    _filter_junk_abnormalities,
    _is_cross_dup,
    _parse_numbered_titles,
    _postprocess_extracted_items,
    _SAFETY_NET_JUNK_RE,
    _validate_suggestion_ownership,
)


# === 跨线去重(结论 vs 指标黄/红) ===

@pytest.mark.parametrize("item,anom,expect", [
    # 22 弘爱: 名称带括号单位缩写/尾缀数字符号 → 归一后相同 → 拦
    ("尿蛋白1+", {"尿蛋白(PRO)", "尿酸(UA)"}, True),
    ("尿酸升高", {"尿酸(UA)", "尿蛋白(PRO)"}, True),
    # 碳13尿素呼气试验阳性 含指标"尿素(BUN)"2 字核心 → 不拦(互含需 ≥3 字)
    ("碳13尿素呼气试验阳性", {"尿素(BUN)", "尿素氮", "肌酐"}, False),
    # 2026-09-12 用户验收: "超重"一律放行(崇左旧口径作废); "体重指数"仍拦
    ("超重", {"体重指数"}, False),
    ("超重", {"体质指数"}, False),
    ("超重", {"身高体重指数"}, False),
    ("超重", {"BMI"}, False),
    # 高密度脂蛋白 类方向词词根匹配
    ("高密度脂蛋白降低", {"高密度脂蛋白(HDL)"}, True),
    # 字符交集阈值 3: 骨密度 vs 低密度脂蛋白胆固醇 共享 {度,密} 不拦
    ("双髋关节骨质密度减少", {"低密度脂蛋白胆固醇"}, False),
    # 5 字前缀变体
    ("乙肝两对半(第1,2,4,5项阳性)", {"乙肝两对半结论"}, True),
])
def test_cross_dup(item, anom, expect):
    assert _is_cross_dup(item, set(anom)) is expect


# === 整串标题/LLM 名展开(_expand_title_segments) ===

def test_expand_split_multi_finding_with_suggestion():
    # 山东"CT：左肺…，必要时随诊；右肺…" 两发现+建议连体
    out = _expand_title_segments("CT：左肺下叶微小纤维结节灶，必要时年度随诊；右肺下叶散在小钙化灶")
    assert out == [("左肺下叶微小纤维结节灶", "必要时年度随诊"),
                   ("右肺下叶散在小钙化灶", "")]


def test_expand_strip_normal_half():
    # 齐鲁"甲状腺结节；甲状腺功能正常" → 正常性后半剥除
    assert _expand_title_segments("甲状腺结节；甲状腺功能正常") == [("甲状腺结节", "")]


def test_expand_split_overlong_multi():
    # 齐鲁 36 字组合标题(4 发现) → 按顿/逗拆
    out = _expand_title_segments(
        "双侧锁骨下动脉狭窄可能，远端动脉血液灌注欠充足、双侧外周动脉僵硬度增高、左侧下肢动脉中层钙化")
    assert [n for n, _ in out] == ["双侧锁骨下动脉狭窄可能", "远端动脉血液灌注欠充足",
                                   "双侧外周动脉僵硬度增高", "左侧下肢动脉中层钙化"]


def test_expand_keep_short_dunhao_name():
    # 莆田"二尖瓣、三尖瓣少量反流"(13 字)顿号名不拆
    assert _expand_title_segments("二尖瓣、三尖瓣少量反流") == [("二尖瓣、三尖瓣少量反流", "")]


def test_expand_strip_check_prefix_and_rads():
    assert _expand_title_segments("甲状腺彩超：甲状腺右叶结节TI-RADS3 类") == [("甲状腺右叶结节", "")]
    assert _expand_title_segments("外科查体：左下肢静脉曲张") == [("左下肢静脉曲张", "")]
    assert _expand_title_segments("CT：左肺下叶微小纤维结节灶") == [("左肺下叶微小纤维结节灶", "")]


def test_expand_unchanged_simple_name():
    assert _expand_title_segments("高甘油三酯血症及高密度脂蛋白降低") == \
        [("高甘油三酯血症及高密度脂蛋白降低", "")]


# === 编号/★/(1)/【】 标题解析(_parse_numbered_titles) ===

def test_parse_star_titles_with_positive_and_direction():
    t = "★  电轴右偏:\n★  碳13尿素呼气试验阳性:提示幽门螺杆菌感染。\n★  血压偏低:"
    titles = _parse_numbered_titles(t)
    assert "电轴右偏" in titles
    assert "碳13尿素呼气试验阳性" in titles
    assert "血压偏低" in titles


def test_parse_paren_number_full_name():
    # 莆田 (n) 子条式: 取冒号前完整名(顿号多段含特征词才产)
    t = "(3)二尖瓣、三尖瓣少量反流:一般临床意义不显著，建议定期复查。\n(1)超重:可见于摄入过多。\n(1)均衡饮食，控制饮食量，不宜过饱:应坚持有氧运动。"
    titles = _parse_numbered_titles(t)
    assert "二尖瓣、三尖瓣少量反流" in titles
    assert "超重" in titles
    assert "均衡饮食" not in titles


def test_parse_bracket_titles():
    # 【】标题词表: 返流(≠反流)/ST段/疾病/改变 均须支持
    t = "【心脏瓣膜返流】本病多与年龄增加有关。\n【ST 段轻度改变】可见于正常人。\n【前列腺疾病】定期复查。\n【肝内血管瘤可能】肝血管瘤是一种常见病。"
    titles = _parse_numbered_titles(t)
    assert "心脏瓣膜返流" in titles
    assert "ST 段轻度改变" in titles
    assert "前列腺疾病" in titles
    assert "肝内血管瘤可能" in titles


def test_parse_bracket_finding_words():
    assert _FINDING_TITLE_RE.search("心脏瓣膜返流")
    assert _FINDING_TITLE_RE.search("ST 段轻度改变")
    assert _FINDING_TITLE_RE.search("前列腺疾病")


def test_safety_net_junk_words():
    # fill 前的垃圾标题过滤词
    assert _SAFETY_NET_JUNK_RE.search("需每半年或一年做一次复检")
    assert _SAFETY_NET_JUNK_RE.search("级代表具有四种以上的恶性征象")
    assert _SAFETY_NET_JUNK_RE.search("岁山东大学齐鲁医院")  # 页眉(医院)
    assert not _SAFETY_NET_JUNK_RE.search("肝内血管瘤可能")   # "可"单字不误杀
    assert not _SAFETY_NET_JUNK_RE.search("子宫肌瘤可能")


# === junk-context 句级判定 ===

def test_junk_context_semicolon_merge():
    # 分号不再是句边界: "肾功能损害"句合并后含"下降到正常" → 判科普
    txt = "在肾功能损害早期，血肌酐可在正常范围；当肾小球滤过率下降到正常的50%以下时才迅速升高。"
    assert _all_occurrences_in_junk_context(txt, "肾功能损害") is True


def test_junk_context_definition_sentence():
    txt = "【肝内血管瘤可能】肝血管瘤是一种较为常见的肝脏良性肿瘤，临床上以海绵状血管瘤最多见。"
    assert _all_occurrences_in_junk_context(txt, "肝血管瘤") is True
    assert _all_occurrences_in_junk_context(txt, "海绵状血管瘤") is True
    # 标题本体(grp 变体)豁免
    assert _all_occurrences_in_junk_context(txt, "肝内血管瘤可能") is False


# === 滤卡(_filter_junk_abnormalities): 挖词滤 / 真名留 ===

_JUNK_TXT = (
    "【肥胖】肥胖已经证实与高血压、冠心病、糖尿病密切相关，严重影响寿命。需要在医生指导下合理控制体重。\n"
    "【肌红蛋白降低】一般降低无临床意义，如您有胸闷、胸痛、心悸等不适，\n建议专科诊治。\n"
    "【肝内血管瘤可能】肝血管瘤是一种较为常见的肝脏良性肿瘤，临床上以海绵状血管瘤最多见。\n"
    "【子宫肌瘤可能】主要表现为月经改变，阴道分泌物增多。\n"
    "★  碳13尿素呼气试验阳性:提示幽门螺杆菌感染。其与消化性溃疡、胃炎及胃癌等相关，"
    "需结合血清幽门螺旋杆菌毒力分型检测结果综合分析，以便确定是否需要幽门螺杆菌感染根除治疗。\n"
    "★  前列腺增大:…如出现尿频、尿急、尿流中断等症状，请至泌尿外科诊治。\n"
    "【双侧颈动脉硬化并左侧斑块形成】颈动脉内膜增厚，是缺血性脑血管病的发病原因之一。\n"
    "【肺结节】根据结节密度，分为纯磨玻璃样结节、实性结节和介于二者之间的混杂性结节。\n"
    "★  肌酐偏高:…在肾功能损害早期，血肌酐可在正常范围；当肾小球滤过率下降到正常的50%以下时才迅速升高。单纯的血肌酐升高，还有可能是甲亢、肢端肥大症等。"
)


@pytest.mark.parametrize("junk_name", [
    "高血压", "冠心病", "糖尿病",           # 肥胖段科普(密切相关/控制体重)
    "胸痛",                               # 断行句+建议专科诊治
    "肝血管瘤", "海绵状血管瘤", "肝脏良性肿瘤",  # 定义句挖词(是一种/临床上以)
    "月经改变", "阴道分泌物增多",           # 主要表现为…
    "幽门螺杆菌感染", "消化性溃疡", "胃炎", "胃癌",  # ★ 碳13…:提示…科普
    "尿流中断",                            # ★ 前列腺…请至泌尿外科诊治
    "缺血性脑血管病",                      # …原因之一
    "纯磨玻璃样结节", "实性结节", "混杂性结节",  # 分级裸词
    "肾功能损害", "甲亢", "肢端肥大症",       # 肌酐段科普(分号合并后)
])
def test_junk_words_filtered(junk_name):
    items = [{"item_name": junk_name, "suggestion": "", "deviation": None,
              "is_urgent": False}]
    kept = _filter_junk_abnormalities(items, source_text=_JUNK_TXT)
    assert kept == [], f"{junk_name!r} 应被滤掉, 实际保留"


@pytest.mark.parametrize("keep_name", [
    "超重", "前列腺增大", "血压偏低", "肥胖", "子宫肌瘤可能", "肝内血管瘤可能",
])
def test_real_names_kept(keep_name):
    # 标题本体/编号条目等不被 junk 误杀(滤卡对真名放行)
    txt = ("★  血压偏低:(1)一次血压低不能确诊。\n"
           "★  前列腺增大:是中老年常见病。\n"
           "【肥胖】肥胖已经证实与高血压密切相关，需要在医生指导下合理控制体重。\n"
           "【肝内血管瘤可能】肝血管瘤是一种较为常见的肝脏良性肿瘤。\n"
           "【子宫肌瘤可能】主要表现为月经改变。\n"
           "超重:体重指数位于24~27.9为超重。")
    items = [{"item_name": keep_name, "suggestion": "", "deviation": None,
              "is_urgent": False}]
    kept = _filter_junk_abnormalities(items, source_text=txt)
    assert kept, f"{keep_name!r} 为真条目, 不应被滤"


# === fill 兜底(_postprocess_extracted_items): LLM 全漏/连体垃圾时补标题 ===

def test_fill_keeps_titles_when_llm_empty():
    text = "★  电轴右偏:\n★  碳13尿素呼气试验阳性:提示幽门螺杆菌感染。\n★  血压偏低:"
    kept, n_fill = _postprocess_extracted_items([], text, raw_text=text)
    names = {i["item_name"] for i in kept}
    assert {"电轴右偏", "碳13尿素呼气试验阳性", "血压偏低"} <= names
    assert n_fill >= 3


def test_fill_after_llm_conjoined_junk_filtered():
    # LLM 输出无分号连体垃圾(齐鲁): 滤卡滤掉后, 标题 fill 仍补"甲状腺结节"
    text = "【甲状腺结节；甲状腺功能正常】您的甲功正常，建议6-12个月复查甲状腺超声。"
    items = [{"item_name": "甲状腺结节甲状腺功能正常", "suggestion": "", "deviation": None,
              "is_urgent": False}]
    kept, _ = _postprocess_extracted_items(items, text, raw_text=text)
    names = [i["item_name"] for i in kept]
    assert "甲状腺结节" in names
    assert "甲状腺结节甲状腺功能正常" not in names


def test_fill_covered_by_clean_llm_name():
    # LLM 已给干净名(窦缓) → fill 不重复补
    text = "★  窦性心动过缓:\n★  前列腺增大:"
    items = [{"item_name": "窦性心动过缓", "suggestion": "", "deviation": None,
              "is_urgent": False}]
    kept, _ = _postprocess_extracted_items(items, text, raw_text=text)
    names = [i["item_name"] for i in kept]
    assert names.count("窦性心动过缓") == 1
    assert names.count("前列腺增大") == 1


# === 建议归属校验(_validate_suggestion_ownership): LLM 跨条目复制建议 ===

_SUGGESTION_TXT = (
    "★  血压偏低:\n(1)一次血压低不能确诊。\n"
    "★  三尖瓣返流(轻度):非常常见，建议每1-2年做一次心脏彩超复查。保持健康体重，低盐饮食，适度锻炼。\n"
    "★  前列腺增大:是中老年常见病。需要耐心的长期治疗。如病情严重，可考虑摘除前列腺。\n"
    "★  窦性心动过缓:\n"
    "★  碳13尿素呼气试验阳性:提示幽门螺杆菌感染。"
)


def test_suggestion_ownership_drops_cross_copied():
    # 前列腺专属建议被贴给 碳13/窦缓 → 清空; 前列腺增大(正主)保留
    items = [
        {"item_name": "碳13尿素呼气试验阳性",
         "suggestion": "需要耐心的长期治疗。如病情严重，可考虑摘除前列腺。"},
        {"item_name": "窦性心动过缓",
         "suggestion": "需要耐心的长期治疗。如病情严重，可考虑摘除前列腺。"},
        {"item_name": "前列腺增大",
         "suggestion": "需要耐心的长期治疗。如病情严重，可考虑摘除前列腺。"},
    ]
    out = _validate_suggestion_ownership(items, _SUGGESTION_TXT)
    got = {i["item_name"]: i["suggestion"] for i in out}
    assert got["碳13尿素呼气试验阳性"] == ""
    assert got["窦性心动过缓"] == ""
    assert "摘除前列腺" in got["前列腺增大"]


def test_suggestion_ownership_keeps_own_and_generic():
    # 自己的复合行建议(含心脏彩超)保留; 通用生活建议句(无器官专属词)不校验不清空
    items = [
        {"item_name": "三尖瓣返流",
         "suggestion": "建议每1-2年做一次心脏彩超复查。保持健康体重，低盐饮食，适度锻炼。"},
        {"item_name": "血压偏低",
         "suggestion": "保持健康体重，低盐饮食，适度锻炼。"},
    ]
    out = _validate_suggestion_ownership(items, _SUGGESTION_TXT)
    got = {i["item_name"]: i["suggestion"] for i in out}
    assert "心脏彩超复查" in got["三尖瓣返流"]
    assert got["血压偏低"] != ""


# === 茂名"检查综述"页中段剥离(_drop_review_block) ===

_REVIEW_TXT = (
    "医 生 建 议：\n"
    "★  血压偏低:\n(1)一次血压低不能确诊为低血压病。\n"
    "★  前列腺增大:是中老年常见病。\n"
    "检 查 综 述：\n"
    "★  体格检查（血压测量）:血压 109/52mmHg：血压偏低\n"
    "★  全腹彩色B超(男):前列腺增大并局部钙化。肝脏未见异常\n"
    "★  心电图:1、窦性心动过缓2、电轴右偏\n"
    "★  心脑\n(1):总胆固醇偏高[6.31];载脂蛋白B偏高[1.12]。\n"
    "(2)如有症状请尽快诊治\n"
    "★  碳13尿素呼气试验阳性:提示幽门螺杆菌感染。其与胃癌等相关。\n"
    "★  电轴右偏:\n（1）生理性情况见于儿童。"
)


def test_drop_review_block_keeps_advice_titles():
    out = _drop_review_block(_REVIEW_TXT)
    assert "检 查 综 述" not in out
    assert "全腹彩色B超" not in out
    assert "体格检查" not in out
    assert "血压偏低" in out
    assert "前列腺增大:是中老年常见病" in out
    assert "碳13尿素呼气试验阳性" in out
    assert "电轴右偏" in out


# === 2026-09-10: 池州人民(USER5)字距空格型文本 ===
# PDF 汉字间逐字空格("1 .肝 内 钙 化灶"/"1 1 、【…】"), 标题解析需先压缩;
# 且子编号行"2.双肾输尿管膀胱未见明显异常"是正常性描述, fill 不得补入。

_CHIZHOU_NUM_TXT = (
    "体检综述\n"
    "1 、【 耳 鼻 喉科】\n"
    "（1 ）鼻 部:鼻 中隔 弯 曲\n"
    "1 0 、 【 肝 胆胰脾 彩 超 】\n"
    "1 .肝 内 钙 化灶\n"
    "2 .胆 、 胰 、脾 未 见 明显 异常\n"
    "1 1 、 【 双 肾输尿 管 膀 胱 前列腺彩超 】\n"
    "1 .前 列 腺 偏大\n"
    "2 .双 肾 输 尿管 膀 胱 未见 明显异常\n"
)


def test_parse_numbered_titles_compacts_spaced_chinese_lines():
    got = _parse_numbered_titles(_CHIZHOU_NUM_TXT)
    # 子编号行的空格形态应被压缩成干净名, 不得留 ".肝"/".胆" 垃圾
    assert "肝内钙化灶" in got
    assert "前列腺偏大" in got
    assert not any(t.startswith(".") or t.startswith(" ") for t in got)
    # 检查科目标题(【耳鼻喉科】等)不是异常条目, 不参与兜底;
    # "未见明显异常"类子编号行解析后由 fill 层拒绝(见下一测试)
    assert not any("耳鼻喉科" in t for t in got)


def test_fill_rejects_normal_and_fragment_titles():
    items, n = _postprocess_extracted_items([], _CHIZHOU_NUM_TXT, raw_text=_CHIZHOU_NUM_TXT)
    names = [(i.get("item_name") or "").replace(" ", "") for i in items]
    assert "双肾输尿管膀胱未见明显异常" not in names
    assert "胆、胰、脾未见明显异常" not in names
    assert "肝内钙化灶" in names
    assert "前列腺偏大" in names


# === 2026-09-10: 扁平化滤卡(字距空格报告 not-in-source/junk-context 误杀回归) ===

_CHIZHOU_FLT_TXT = (
    "体检综述\n"
    "2 、【 糖 化 血红蛋 白 （ 粉 管）】\n"
    "抗碱 血红 蛋白偏 高\n"
    "8 、【 心 脏 彩超】\n"
    "二尖 瓣轻 度返流\n"
    "健康指导建议\n"
    "*  二 尖 瓣 轻 度 返 流:\n"
    "(1) 建议 您 定期复查 心 脏 彩 超 。\n"
)


def test_junk_filter_flat_keeps_llm_items_from_spaced_source():
    from app.modules.report.service import _filter_junk_abnormalities
    items = [
        {"item_name": "二尖瓣轻度返流", "suggestion": "", "deviation": None, "is_urgent": False},
        {"item_name": "抗碱血红蛋白偏高", "suggestion": "", "deviation": None, "is_urgent": False},
    ]
    out = _filter_junk_abnormalities(items, source_text=_CHIZHOU_FLT_TXT)
    names = [i["item_name"] for i in out]
    # 名字在原文中以字距空格形态存在, 扁平化比较后放行(此前整批误杀)
    assert "二尖瓣轻度返流" in names
    assert "抗碱血红蛋白偏高" in names


# === 2026-09-10: 椎间盘条目口径(用户) ===
# 剥节段/部位前缀, 只留异常名, 且方向词保留原文:
# "L3-4、L4-5及L5-S1椎间盘膨出"→"椎间盘膨出"; 突出报告不得被改成膨出。
# 纯节段残片("L3-4")不是独立异常, 滤除。

def test_disc_strip_prefix_keep_direction():
    cases = {
        "L4-5 及 L 5 - S 1 椎 间盘膨 出": "椎间盘膨出",
        "L3-4、L4-5及L5-S1椎间盘膨出,随诊": "椎间盘膨出",
        "颈3/4、颈5/6椎间盘向后突出": "椎间盘突出",
        "椎间盘突出": "椎间盘突出",
        "腰椎间盘膨出": "椎间盘膨出",
    }
    for n, want in cases.items():
        out = _filter_junk_abnormalities(
            [{"item_name": n, "suggestion": "", "deviation": None, "is_urgent": False}],
            source_text="x", title_names=[])
        assert out and out[0]["item_name"] == want, f"{n!r} -> {out!r} (want {want})"


def test_disc_pure_segment_fragment_filtered():
    out = _filter_junk_abnormalities(
        [{"item_name": "L3 - 4", "suggestion": "", "deviation": None, "is_urgent": False}],
        source_text="x", title_names=[])
    assert out == []


# === 2026-09-10: 结论兜底标题过滤 / 泛称去重(用户报告三例) ===
def test_fallback_title_junk_filter():
    from app.modules.report.service import _is_junk_fallback_title, _parse_numbered_titles
    assert _is_junk_fallback_title("胸部CT")
    assert _is_junk_fallback_title("甲状腺B")
    assert _is_junk_fallback_title("腹部B")
    assert _is_junk_fallback_title("肺结节是指肺内直径≤3cm的类圆形或不规则形病灶")
    assert not _is_junk_fallback_title("右肺尖间隔旁型肺气肿")
    titles = _parse_numbered_titles(
        "2、胸部CT 平扫：右肺尖间隔旁型肺气肿。\n"
        "3、甲状腺B 超：甲状腺双叶多发囊性结节，大者0.3cm×0.2cm。")
    assert "胸部CT" not in titles and "甲状腺B" not in titles


def test_dedup_generic_findings():
    from app.modules.report.service import _dedup_generic_findings
    items = [{"item_name": "肥胖"}, {"item_name": "轻度肥胖"},
             {"item_name": "肺结节"}, {"item_name": "右肺中叶内侧段微小结节"}]
    names = [i["item_name"] for i in _dedup_generic_findings(items)]
    assert "轻度肥胖" in names and "肥胖" not in names
    assert "右肺中叶内侧段微小结节" in names and "肺结节" not in names


# === 2026-09-11: junk 检查必须用未加工原文(加工裁句会漏滤"胸痛"类科普挖词) ===
def test_junk_context_uses_raw_source():
    from app.modules.report.service import _postprocess_extracted_items
    ct = ("【肌红蛋白降低】存在于心脏和骨骼的横纹肌中。一般降低无临床意义，"
          "如您有胸闷、胸痛、心悸等不适，建议专科诊治。")
    out, _ = _postprocess_extracted_items([{"item_name": "胸痛"}], "裁剪文本占位",
                                          raw_text=ct)
    assert "胸痛" not in [i["item_name"] for i in out]


def test_new_generic_dedup_and_expand():
    from app.modules.report.service import _dedup_generic_findings, _expand_title_segments
    items = [{"item_name": "双肺散在小结节"}, {"item_name": "多系炎性结节"},
             {"item_name": "双肺散在小结节，多系炎性结节"},
             {"item_name": "牙结石（+）"}, {"item_name": "47龋齿"}]
    names = [i["item_name"] for i in _dedup_generic_findings(items)]
    assert "双肺散在小结节，多系炎性结节" not in names
    assert "牙结石" in names and "龋齿" in names
    segs = _expand_title_segments("低密度脂蛋白胆固醇增高，载脂蛋白B 增高，超重")
    assert any(n == "超重" for n, _s in segs)


# === 2026-09-10: 名称归一入口化(_write_norm / _cmp_norm) ===

def test_cmp_norm_unifies_forms():
    from app.modules.report.service import _write_norm, _cmp_norm
    # 书写归一: 全角标点/空格
    assert _write_norm("腹部B 超") == "腹部B超"
    assert _write_norm("尿蛋白（PRO）") == "尿蛋白(PRO)"
    # 比较归一: 剥检查前缀/括号/结论测定/尾数字符号
    assert _cmp_norm("彩超提示肝囊肿") == "肝囊肿"
    assert _cmp_norm("尿酸(UA)") == "尿酸"
    assert _cmp_norm("乙肝两对半结论") == "乙肝两对半"
    assert _cmp_norm("尿蛋白1+") == "尿蛋白"
    # 方向词保留(调用方按需剥离)
    assert _cmp_norm("间接胆红素偏高") == "间接胆红素偏高"


# === 2026-09-10: 多方向行规则候选(_parse_direction_phrases, 结构收权) ===

def test_direction_phrases_multi_abnormal_line():
    from app.modules.report.service import _parse_direction_phrases
    txt = ("3 、【 生 化 Ⅱ】\n"
           "间接 胆红 素偏高 载脂蛋白E 偏低  脂 蛋 白( a )偏 高\n"
           "尿潜 血( B L D) +1  尿 比 重 偏 高 酸 碱 度 偏低  维生 素 C 弱阳性  红 细 胞 计数 偏高\n")
    got = _parse_direction_phrases(txt)
    for want in ("间接胆红素偏高", "载脂蛋白E偏低", "脂蛋白(a)偏高",
                 "尿潜血(BLD)+1", "尿比重偏高", "酸碱度偏低",
                 "维生素C弱阳性", "红细胞计数偏高"):
        assert want in got, f"{want} missing in {got}"


def test_direction_phrases_skip_single_and_advice_lines():
    from app.modules.report.service import _parse_direction_phrases
    txt = ("抗碱 血红 蛋白偏 高\n"                      # 单方向行: 不产
           "建议 您 定期复查 心 脏 彩 超 。\n"          # 建议行(含方向词?无成对): 不产
           "如出 现 胸 闷 、 心 悸 等 不 适 ， 请 及 时 就 诊 ， 避 免 病 情 加 重\n")
    assert _parse_direction_phrases(txt) == []


def test_direction_candidate_covered_by_llm_stem():
    # LLM 给"间接胆红素"(dev=偏高) → 不再补"间接胆红素偏高"(词根覆盖)
    txt = "间接 胆红 素偏高 载脂蛋白E 偏低  脂 蛋 白( a )偏 高\n"
    items = [{"item_name": "间接胆红素", "suggestion": "", "deviation": "偏高", "is_urgent": False}]
    out, _ = _postprocess_extracted_items(items, txt, raw_text=txt, weak_candidates=True)
    names = [i["item_name"].replace(" ", "") for i in out]
    assert "间接胆红素" in names and "间接胆红素偏高" not in names
    assert "载脂蛋白E偏低" in names or "载脂蛋白E" in names


def test_weak_candidates_default_off():
    # 2026-09-12 用户口径: 弱切分(多发现/多方向行)默认关闭, 仅 profile 声明院启用
    txt = "间接 胆红 素偏高 载脂蛋白E 偏低  脂 蛋 白( a )偏 高\n"
    items = [{"item_name": "间接胆红素", "suggestion": "", "deviation": "偏高", "is_urgent": False}]
    out, _ = _postprocess_extracted_items(items, txt, raw_text=txt)  # 默认 False
    names = [i["item_name"].replace(" ", "") for i in out]
    assert "载脂蛋白E偏低" not in names, "默认不应启用弱切分候选"


def test_multi_findings_profile_flags():
    # 声明院: 池州/福建第二/广西人民 启用; 其它默认关
    from app.modules.report.report_profiles import match_profile
    assert match_profile("池州市人民医院 体检报告").get("multi_findings") is True
    assert match_profile("福建省第二人民医院").get("multi_findings") is True
    assert match_profile("广西壮族自治区人民医院").get("multi_findings") is True
    assert match_profile("某不存在的医院").get("multi_findings") is False


# === 2026-09-10: LLM 名契约 —— 幻觉名(源文不可定位)必须被滤 ===

def test_llm_hallucinated_name_filtered():
    txt = "★  窦性心动过缓:\n★  前列腺增大:"
    items = [{"item_name": "十二指肠溃疡", "suggestion": "", "deviation": None,
              "is_urgent": False}]
    from app.modules.report.service import _filter_junk_abnormalities
    out = _filter_junk_abnormalities(items, source_text=txt, title_names=[])
    assert out == [], "源文中不存在该名, 应作为幻觉滤除"


# === 2026-09-11: 终检去重(泛词碎片/同义写法) ===

def _items(*names):
    return [{"item_name": n, "suggestion": "", "deviation": None, "is_urgent": False}
            for n in names]


def test_generic_finding_dropped_when_qualified_exists():
    from app.modules.report.service import _dedup_generic_findings
    out = _dedup_generic_findings(_items("结节", "肺结节", "钙化灶", "肝内钙化灶", "前列腺偏大"))
    names = [i["item_name"] for i in out]
    assert "结节" not in names and "钙化灶" not in names
    assert "肺结节" in names and "肝内钙化灶" in names and "前列腺偏大" in names


def test_generic_finding_filtered_even_without_qualified():
    # 2026-09-12 收紧: 纯泛词本身不是独立异常, 无条件滤(日照"钙化"误落展示)
    from app.modules.report.service import _dedup_generic_findings
    out = _dedup_generic_findings(_items("结节", "钙化"))
    assert out == []


def test_synonym_dedup_urine_occult_blood():
    from app.modules.report.service import _dedup_generic_findings
    out = _dedup_generic_findings(_items("尿隐血", "尿潜血(BLD)+1"))
    assert len(out) == 1
    # 保留更长(信息更全)者
    assert out[0]["item_name"] == "尿潜血(BLD)+1"


# === 2026-09-12: 端到端抽查暴露的两条垃圾名 ===

def test_method_prompt_prefix_stripped_for_keju_variants():
    from app.modules.report.service import _strip_check_prefix
    assert _strip_check_prefix("口腔科提示18") == "18"


def test_junk_names_from_trial_run_filtered():
    from app.modules.report.service import _filter_junk_abnormalities
    txt = ("口腔科\n口腔科提示:18、28、38牙智齿\n"
           "直接胆红素:不一定有临床意义,如连续多次升高")
    items = [
        {"item_name": "口腔科提示18", "suggestion": "", "deviation": None, "is_urgent": False},
        {"item_name": "不一定有临床意义,如连续多次升高", "suggestion": "",
         "deviation": None, "is_urgent": False},
    ]
    out = _filter_junk_abnormalities(items, source_text=txt, title_names=[])
    assert out == [], out


def test_fill_rejects_sentence_and_method_fragment_titles():
    # fill 在滤卡之后, 需自拒: 句子式标题/方法前缀残片(2026-09-12 端到端抽查)
    txt = ("8.不一定有临床意义,如连续多次升高\n"
           "口腔科提示18、28、38牙智齿\n"
           "2、前列腺偏大\n")
    out, _ = _postprocess_extracted_items([], txt, raw_text=txt)
    names = [i["item_name"].replace(" ", "") for i in out]
    assert "不一定有临床意义,如连续多次升高" not in names
    assert "口腔科提示18" not in names
    assert "前列腺偏大" in names


def test_direction_phrases_skip_section_guide_lines():
    # 滨州"▍异常指标解读以下按照疾病诊断、阳性发现和其他异常，列出…" 引导句
    # 含多个方向词但非条目, 不得切出"发现和其他异常"/"指标解读以下按照疾病诊断阳性"
    from app.modules.report.service import _parse_direction_phrases
    txt = "▍异常指标解读以下按照疾病诊断、阳性发现和其他异常，列出本次体检所发现的问题\n"
    assert _parse_direction_phrases(txt) == []


def test_anatomy_only_and_section_junk_filtered():
    from app.modules.report.service import _filter_junk_abnormalities
    txt = "8.二尖瓣、三尖瓣轻度反流多属于生理性，不需特殊处理。"
    out = _filter_junk_abnormalities(
        [{"item_name": "二尖瓣", "suggestion": "", "deviation": None, "is_urgent": False}],
        source_text=txt, title_names=[])
    assert out == [], out


def test_numbered_title_unclosed_bracket_across_lines():
    # 马鞍山 25: "6、[甲状腺结节,\n考虑C-TIRADS3 类]" 跨行未闭合 → 拼接取段
    from app.modules.report.service import _parse_numbered_titles
    txt = "5、[三尖瓣少量反流]\n6、[甲状腺结节,\n考虑C-TIRADS3 类]\n7、[中性粒细胞偏低"
    got = _parse_numbered_titles(txt)
    assert "甲状腺结节" in got and "三尖瓣少量反流" in got


def test_pulmonary_nodule_generic_only_same_lung():
    # "肺结节"泛称只有当同批存在同部位(含"肺")更精确结节条目时才删;
    # 不得因"甲状腺结节"误删报告方真标题(马鞍山 25)
    from app.modules.report.service import _dedup_generic_findings
    kept = _dedup_generic_findings(_items("甲状腺结节", "肺结节"))
    assert "肺结节" in [i["item_name"] for i in kept]
    dropped = _dedup_generic_findings(_items("肺结节", "右肺中叶内侧段微小结节"))
    assert "肺结节" not in [i["item_name"] for i in dropped]


def test_fill_rejects_method_prompt_number_fragment():
    # 潮州 23: "彩超检查提示1" 由 fill 产出 → 拒
    txt = "彩超检查提示1\n2、前列腺偏大\n"
    out, _ = _postprocess_extracted_items([], txt, raw_text=txt)
    names = [i["item_name"].replace(" ", "") for i in out]
    assert "彩超检查提示1" not in names


def test_direction_phrases_reject_prose_and_circle_number():
    # 柳州(H004-1)重跑回归: 科普/建议连续句 + 圈号序号头 不得切出候选
    # ("检查化验结果略有异常"/"肝功能异常"/"①血脂异常"/"喝茶也可使血脂水平下降")
    from app.modules.report.service import _parse_direction_phrases
    txt = (
        "检查化验结果略有异常，具体根据以下体检诊断建议进行相应诊治，3-6个月定期进行异常指标复查。\n"
        "血脂偏高，肝功能异常者到健康管理中心门诊就诊，在医师指导下降酶降脂治疗。定期复查血脂、肝功及B超。\n"
        "①血脂异常是一种血脂代谢异常引起的疾病，分为遗传性和环境因素引起。\n"
        "运动可使血脂水平下降。喝茶也可使血脂水平下降，特别是喝绿茶，但是喝茶可以使钙、铁吸收障碍。\n"
    )
    assert _parse_direction_phrases(txt) == []


# === 2026-09-12 H003 验收批(用户逐家报错) ===

def test_conjoined_fatty_liver_overweight_split():
    # 崇左: 【脂肪肝】【超重】被拼成"脂肪肝超重" → 拆两条
    txt = "【脂肪肝】【超重】脂肪肝(脂肪性肝病)是以…"
    items = [{"item_name": "脂肪肝超重", "suggestion": "", "deviation": None, "is_urgent": False}]
    out, _ = _postprocess_extracted_items(items, txt, raw_text=txt)
    names = [i["item_name"] for i in out]
    assert "脂肪肝" in names and "超重" in names and "脂肪肝超重" not in names


def test_true_name_ending_with_obesity_not_split():
    txt = "向心性肥胖"
    items = [{"item_name": "向心性肥胖", "suggestion": "", "deviation": None, "is_urgent": False}]
    out, _ = _postprocess_extracted_items(items, txt, raw_text=txt)
    assert "向心性肥胖" in [i["item_name"] for i in out]


def test_measure_desc_and_prose_tail_filtered():
    # 2026-09-12 口径更新: "腹型肥胖/腰臀比"黑名单移除(防城港一"腹型肥胖"是
    # DB 验收真条目); 贵港多提由 profile 切段(仅"异常指标+健康建议")解决。
    from app.modules.report.service import _postprocess_extracted_items
    txt = "（2）病理性红细胞增多见于：地中海贫血等"
    items = [
        {"item_name": "病理性红细胞增多见于", "suggestion": "", "deviation": None, "is_urgent": False},
    ]
    out, _ = _postprocess_extracted_items(items, txt, raw_text=txt)
    assert [i["item_name"] for i in out] == []


def test_imaging_description_tail_trimmed():
    txt = "7、右肺下叶微小磨玻璃类结节，较前相仿。"
    items = [{"item_name": "右肺下叶微小磨玻璃类结节，较前相仿", "suggestion": "",
              "deviation": None, "is_urgent": False}]
    out, _ = _postprocess_extracted_items(items, txt, raw_text=txt)
    assert [i["item_name"] for i in out] == ["右肺下叶微小磨玻璃类结节"]
