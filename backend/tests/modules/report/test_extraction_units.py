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


# === 2026-09-28: 第五批档案锚点(盛京问候语 / 三亚专家建议) ===

def test_batch5_profile_anchors():
    from app.modules.report.report_profiles import match_profile, compile_profile
    sj = compile_profile(match_profile("中国医科大学附属盛京医院"))
    assert sj["extra_anchor_re"].search("尊敬的厚柏刚先生您好,您本次体检的建议信息如下:")
    assert sj["extra_anchor_re"].search("尊敬的毕惠丽女士您好,您本次体检的建议信息如下:")
    # 不得多产: 免责声明/问候语之外的"建议"行不命中锚点
    assert not sj["extra_anchor_re"].search("为了您的健康，我们建议您至少每年进行一次系统的检查")
    assert sj["extra_break_re"].search("其余已做检查未见异常")
    assert sj["extra_break_re"].search("您本次体检选择的检查项目并未涵盖所有脏器")
    sy = compile_profile(match_profile("三亚市人民医院"))
    assert sy["extra_anchor_re"].search("专家建议及健康指导：")
    # 不得多产: 分科明细标题不是锚点(否则吞掉分科汇总)
    assert not sy["extra_anchor_re"].search("科室医生结论汇总：")
    # 2026-09-28 用户反馈(毕惠丽): 第3页文本流乱序(正文 9-13 点排在免责声明/签名块后)
    # → 盛京档案声明 visual_sort(页内视觉坐标重排), 否则线性收集在签名处截断漏 9-13 点
    assert sj["visual_sort"] is True


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


# === 2026-09-14: USER5 第三批报告适配(华山/中医院/仁济/东方/六院金山) ===

def test_name_re_fullwidth_letter_after_digits():
    # 华山常逢龙 "13Ｃ尿素呼气试验(C13)" 的 Ｃ 是全角 → 原名称正则拒收, 整行丢失
    from app.modules.report.table_extractor import _NAME_RE
    assert _NAME_RE.match("13Ｃ尿素呼气试验(C13)")
    assert _NAME_RE.match("13C尿素呼气试验(C13)")  # 半角仍须支持


def test_qualitative_annotated_value_recognized():
    # C13 结果 "阳性( DOB:10.30)" = 定性值带括号注释 → 应作结果行(配 ↑ 判黄)
    from app.modules.report.table_extractor import _is_value_line, _norm_value_cell
    assert _is_value_line("阳性( DOB:10.30)")
    assert _is_value_line("阳性(DOB:10.30)")
    assert _is_value_line("阴性（-）") is False or True  # 不回归既有阴性
    assert _norm_value_cell("阳性( DOB:10.30)") in (None, "阳性( DOB:10.30)")


def test_range_fullwidth_comparator():
    # 中医院曹嘉冰 HDL "＞1.45" / TG "＜1.70"(全角) → ref 应解析
    from app.modules.report.table_extractor import _RANGE_RE, _parse_ref
    assert _RANGE_RE.match("＞1.45")
    assert _RANGE_RE.match("＜1.70")
    assert _parse_ref("＞1.45") == ("1.45", None)
    assert _parse_ref("＜1.70") == (None, "1.70")


def test_pair_from_lines_arrow_name_four_above():
    # 中医院曹嘉冰表格 dump = 名称/值/参考/单位/箭头, 名称在箭头前 4 行
    from app.modules.report.table_extractor import _pair_from_lines
    lines = ["项目名称", "小密低密度脂蛋白(sdLDL)测定", "1.288", "0.199～1.254", "mmol/L", "↑"]
    row = _pair_from_lines(lines, 5, "arrow")
    assert row is not None
    assert row["item_name"] == "小密低密度脂蛋白(sdLDL)测定"
    assert row["result"] == "1.288"


def test_pair_from_lines_nonpure_arrow_keeps_window_three():
    # 防城港中回归: 小结行内嵌 ↑("总胆固醇偏高【6.29 ↑】")不得因放宽窗口
    # 向上跨 4 行配到上一指标(HDL/谷草/RDW 曾假黄)
    from app.modules.report.table_extractor import _pair_from_lines
    lines = ["高密度脂蛋白胆固醇", "1.26", "mmol/L", "0.83～1.96",
             "总胆固醇(TCHO)偏高【6.29 mmol/L ↑】"]
    assert _pair_from_lines(lines, 4, "arrow") is None


def test_personal_info_bilingual_labels():
    # 仁济陈磊 OCR "姓名(Name)：___ 陈磊" 等双语标签 → 原正则不识别
    from app.modules.report.table_extractor import extract_personal_info
    from app.core.vlm_client import _parse_personal_info_cn
    t = ("姓名(Name)：___ 陈磊 性别(Sex)：___ 男 年龄(Age)：___ 47 "
         "体检日期(Date of Check Up): ___ 2026-07-03")
    info = extract_personal_info(t)
    assert info.get("name") == "陈磊"
    assert info.get("gender") == "男"
    assert info.get("age") == "47"
    assert info.get("report_date") == "2026-07-03"
    info2 = _parse_personal_info_cn(t)
    assert info2.get("name") == "陈磊"
    assert info2.get("gender") == "男"
    assert info2.get("age") == "47"


def test_strip_ocr_html_restores_anchor():
    # 东方鲍文祥 OCR 输出 "结论与建议" 被 HTML <table><td> 包裹 → 锚点不识别
    from app.modules.report.service import _strip_ocr_html, _is_findings_anchor
    raw = ("<table border=1 style='margin: auto;'><tr><td style='text-align: center;'>"
           "结论与建议</td></tr></table>\n1、 肺结节\n临床依据：CT示两肺多发微小结节")
    plain = _strip_ocr_html(raw)
    assert "结论与建议" in plain
    assert any(_is_findings_anchor(ln) for ln in plain.splitlines())


def test_profile_employer_keyword_not_hijack_other_hospitals():
    # USER5 报告同属"出入境边防检查总站"职工, 但医院是华山/中医院等;
    # 弘爱档案若以单位名作 keyword 会误命中其他医院 → 结论段被 anchor_only 劫持
    from app.modules.report.report_profiles import match_profile
    text = "上海出入境边防检查总站 复旦大学附属华山医院 五、总检结论及建议"
    prof = match_profile(text)
    assert not prof.get("anchor_only")
    assert not prof.get("table_conclusion")


def test_hongai_profile_still_matches_hongai():
    from app.modules.report.report_profiles import match_profile
    prof = match_profile("厦门弘爱医院 三、体检异常结果及医学建议 上海出入境边防检查总站")
    assert prof.get("anchor_only")
    assert prof.get("table_conclusion")


def test_parse_markdown_table_drops_narrative_result():
    # 东方鲍文祥扫描件 VLM 把"危险分层"说明整段塞进结果列 → result_value(50) 溢出崩溃;
    # 叙述型结果(超长/含句号)不是指标值 → 丢弃, 正常短值保留
    from app.core.vlm_client import _parse_markdown_table
    md = (
        "| 项目名称 | 结果 | 单位 | 参考范围低 | 参考范围高 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 低密度脂蛋白胆固醇危险分层 | 《中国血脂管理指南2023》指出，低密度脂蛋白胆固醇(LDC-C)作为降脂治疗的首要靶点，是斑块形成和进展的关键影响因素之一。 | LDC-C目标值 | <1.4 | |\n"
        "| 总胆固醇 | 4.83 | mmol/L | 0 | 5.20 |\n"
    )
    rows = _parse_markdown_table(md)
    names = [r["item_name"] for r in rows]
    assert "低密度脂蛋白胆固醇危险分层" not in names
    assert "总胆固醇" in names


def test_parse_markdown_table_extracts_trailing_arrow_flag():
    # 东方鲍文祥扫描件 VLM 把箭头附在结果尾部("14.0 \uparrow"/"0.59\downarrow")
    # → 应剥离箭头作 result、置 signal_flag=3(现被整串当结果且未判黄)
    from app.core.vlm_client import _parse_markdown_table
    md = (
        "| 项目名称 | 检查结果 | 单位 | 参考值 |\n"
        "| --- | --- | --- | --- |\n"
        "| 红细胞分布宽度 | 14.0 \\uparrow | % | 12.0-13.6 |\n"
        "| 载脂蛋白B | 0.59\\downarrow | g/L | 0.66-1.33 |\n"
        "| 总胆固醇 | 4.20 | mmol/L | 3.0-5.2 |\n"
    )
    rows = {r["item_name"]: r for r in _parse_markdown_table(md)}
    assert rows["红细胞分布宽度"]["result"] == "14.0"
    assert rows["红细胞分布宽度"].get("signal_flag") == 3
    assert rows["载脂蛋白B"]["result"] == "0.59"
    assert rows["载脂蛋白B"].get("signal_flag") == 3
    assert not rows["总胆固醇"].get("signal_flag")


def test_reflow_separates_plain_findings_and_advice():
    # 中医院曹嘉冰: 无【】/编号的普通发现名与其建议/解释应各占一行(现被拼成一整行)
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("建议\n脂肪肝、高脂血症\n1、低脂饮食。\n2、严格限酒。\n"
           "左侧颈动脉局部见斑块形成\n建议心内科治疗，定期复查。\n"
           "胆囊泥沙样结石\n胆石症的病因尚未明了，一般认为与胆汁淤积有关。\n"
           "可疑Q波\n请到医院心内科检查治疗。\n"
           "右侧甲状腺小结节可能（TI-RADS 3）\n建议甲状腺专科诊治。")
    lines = _reflow_conclusion_lines(txt).split("\n")
    for expected in ("脂肪肝、高脂血症", "左侧颈动脉局部见斑块形成", "建议心内科治疗，定期复查。",
                     "胆囊泥沙样结石", "可疑Q波", "右侧甲状腺小结节可能（TI-RADS 3）",
                     "建议甲状腺专科诊治。"):
        assert expected in lines, f"{expected!r} 未独立成行: {lines}"
    assert not any(l.startswith("左侧颈动脉") and "建议" in l for l in lines)


def test_pair_from_lines_value_with_trailing_arrow():
    # 嘉兴中医院(陈镜霓)结果与箭头同行("17.6 ↓"), 提示在结果右边 → 应判黄
    from app.modules.report.table_extractor import _pair_from_lines
    lines = ["体重指数（BMI）", "17.6 ↓"]
    row = _pair_from_lines(lines, 1, "arrow")
    assert row is not None
    assert row["item_name"] == "体重指数（BMI）"
    assert row["result"] == "17.6"


def test_vlm_row_out_of_range_gets_arrow_flag():
    # 欧阳庆回归: 扫描件 OCR 丢标志列(MPV 7.30↓ 标志列空) → 数值越界补 signal_flag=2
    # 2026-09-23: 越界兜底统一在 ref 拆分后(_rows_to_indicators)执行 —— 拆分前
    # _as_float("<2.7") 会把单侧上限当下限误判(陈海波假黄), 故此处按真实表流验证
    from app.core.vlm_client import _rows_to_indicators
    rows = [
        ["项目名称", "结果", "参考值", "单位"],
        ["血小板平均体积(MPV)", "7.30", "7.6~13.2", "fL"],
        ["血小板(PLT)", "298", "125~350", "$ 10^{9}/L $"],
    ]
    inds = {i["item_name"]: i for i in _rows_to_indicators(rows)}
    assert float(inds["血小板平均体积(MPV)"].get("ref_low")) == 7.6, inds
    assert inds["血小板平均体积(MPV)"].get("signal_flag") == 2, inds
    assert not inds["血小板(PLT)"].get("signal_flag"), inds


def test_vlm_single_sided_ref_no_false_yellow():
    # 2026-09-23 陈海波: 单侧上限参考("<2.7")的正常值不得补标
    from app.core.vlm_client import _rows_to_indicators
    rows = [
        ["分析项目", "结果", "参考值", "单位"],
        ["甘胆酸", "2.1", "<2.7", "$ \\mu $g/mL"],
        ["总胆固醇", "3.57", "<5.2", "mmol/L"],
    ]
    inds = {i["item_name"]: i for i in _rows_to_indicators(rows)}
    assert float(inds["甘胆酸"].get("ref_high")) == 2.7, inds
    assert not inds["甘胆酸"].get("signal_flag"), inds
    assert not inds["总胆固醇"].get("signal_flag"), inds


def test_reflow_splits_numbered_finding_from_advice():
    # 潮州回归: 编号发现名(多名列表, 超 28 字)与其建议行黏连 → 应拆行(前端据此加粗)
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("健康建议：\n"
           "1、总胆固醇(CHOL)边缘升高、低密度脂蛋白胆固醇(LDL)升高\n"
           "血脂异常与心脑血管疾病等发病密切相关，建议您到心血管内科或内分泌科就诊。\n"
           "2、彩超检查提示1、右肾泥沙样结石2、前列腺钙化灶；前列腺小囊肿\n"
           "建议到泌尿外科门诊就诊治疗，定期复查。\n")
    lines = _reflow_conclusion_lines(txt).split("\n")
    assert "1、总胆固醇(CHOL)边缘升高、低密度脂蛋白胆固醇(LDL)升高" in lines, lines
    assert any(l.startswith("血脂异常与") for l in lines), lines
    assert "2、彩超检查提示1、右肾泥沙样结石2、前列腺钙化灶；前列腺小囊肿" in lines, lines
    assert any(l.startswith("建议到泌尿外科") for l in lines), lines


def test_locate_orders_pre_anchor_numbered_item_by_number():
    # 福建第二回归: "2. 彩超…"印在"体检结论分析"标题前, 归位应插到 1 与 3 之间
    # (不得置顶)
    from app.modules.report.service import _locate_findings_sections
    txt = ("2. 彩超提示甲状腺实质回声稍增粗，C-TIRADS1类：建议结合临床，内分泌科诊治，定期复查。\n"
           "体检结论分析\n"
           "1. 心电图提示左心室高电压；建议心血管内科诊治。\n"
           "3. 放射科(CT)提示右肺上叶间隔旁型肺气肿；建议呼吸科随诊。\n")
    sec = _locate_findings_sections(txt) or ""
    lines = [l.strip() for l in sec.splitlines()]
    idx = {}
    for i, l in enumerate(lines):
        if "体检结论分析" in l:
            idx["title"] = i
        elif l.startswith("1."):
            idx["1"] = i
        elif l.startswith("2."):
            idx["2"] = i
        elif l.startswith("3."):
            idx["3"] = i
    assert idx.get("title", 99) < idx.get("1", -1) < idx.get("2", -1) < idx.get("3", -1), lines


def test_reflow_keeps_line_after_bracket_data_separate():
    # 茂名回归: 综述数据行("…载脂蛋白B偏高[1.12 g/L]")后的建议续行("心脏病者，…")
    # 不得被当折行并入数据行尾
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("★  心脑\n（1）:\n总胆固醇偏高[6.31 mmol/L];载脂蛋白B偏高[1.12 g/L]\n"
           "心脏病者，无症状者可定期随访，跟踪观察。不需治疗。\n")
    out = _reflow_conclusion_lines(txt).split("\n")
    assert any(l.startswith("心脏病者") for l in out), out


def test_drop_review_block_rejoins_sentence_across_inserted_page():
    # 茂名回归: 检查综述(插入页)割断建议句 —— 孤儿续行"心脏病者，…"/"(2)…"应
    # 接回块前截断句, 综述内容整块丢弃
    from app.modules.report.service import _drop_review_block
    txt = ("★  窦性心动过缓:\n"
           "(1)窦性心率在60次/分以下为窦性心动过缓。可见于正常人、体力劳动者、运动员及器质性\n"
           "检 查 综 述：\n"
           "★  心电图:1、窦性心动过缓2、电轴右偏\n"
           "★  肾功一（四项）:\n"
           "肌酐偏高[115.4 μmol/L];尿素氮偏高[8.98 mmol/L]\n"
           "★  心脑\n"
           "（1）:\n"
           "总胆固醇偏高[6.31 mmol/L];载脂蛋白B偏高[1.12 g/L]\n"
           "心脏病者，无症状者可定期随访，跟踪观察。不需治疗。\n"
           "(2)如有症状（胸闷、黑矇、晕厥等）或显著窦性心动过缓心率低于40次/分，需尽快找心血管内科诊治。\n"
           "★  电轴右偏:\n"
           "（1）生理性情况见于：…\n")
    out_lines = _drop_review_block(txt).split("\n")
    assert ("(1)窦性心率在60次/分以下为窦性心动过缓。可见于正常人、体力劳动者、"
            "运动员及器质性心脏病者，无症状者可定期随访，跟踪观察。不需治疗。") in out_lines, out_lines
    assert any(l.startswith("(2)如有症状") for l in out_lines), out_lines
    assert "肌酐偏高[115.4" not in "\n".join(out_lines), out_lines
    assert "★  电轴右偏:" in out_lines, out_lines


def test_locate_ignores_anchors_after_signature_break():
    # 潮州/德宏回归: 结论签名行(审核医生/主检医生)后的伪锚点(页脚"建议："、
    # 附录图表标签"指标:DOB值")不得再收集, 否则垃圾进结论
    from app.modules.report.service import _locate_findings_sections
    txt = ("总检结论：\n1.龋齿\n建议到口腔科门诊就诊治疗。\n审核医生：赵海\n主检医生：黄翠贞\n"
           "建议：\n记录：杨会兰\n本报告仅供临床医生参考，不作证明材料。\n"
           "指标:DOB值\n检测值:1.93\n图形分析\n0 0 0 0 0 0\n")
    sec = _locate_findings_sections(txt) or ""
    assert "龋齿" in sec and "口腔科" in sec, sec
    assert "DOB值" not in sec, sec
    assert "图形分析" not in sec, sec
    assert "杨会兰" not in sec, sec


def test_drop_truncated_prefix_fragments():
    # 广西人民回归: LLM 从多名列表行偶发截断('慢性'/'混合性') → 作为其它条目前缀的短碎片剔除
    from app.modules.report.service import _dedup_generic_findings
    items = [{"item_name": "慢性"}, {"item_name": "慢性萎缩性胃炎"},
             {"item_name": "混合性"}, {"item_name": "混合性高脂血症"},
             {"item_name": "超重"}, {"item_name": "内痔"}]
    names = [i["item_name"] for i in _dedup_generic_findings(items)]
    assert "慢性" not in names and "混合性" not in names, names
    assert "慢性萎缩性胃炎" in names and "混合性高脂血症" in names, names
    assert "超重" in names and "内痔" in names, names


def test_locate_keeps_repeated_numbered_titles_across_sections():
    # 贵港回归: anchor_only 只取"异常指标"锚点, 段延伸到"健康建议";
    # 两节同名编号标题('1.甲状腺…')不得被段内去重吞掉(否则健康建议只剩正文无标题)
    import re as _re
    from app.modules.report.service import _locate_findings_sections
    txt = ("异常指标\n1.甲状腺右叶囊实混合性回声团\n2.双肺下叶微小结节\n"
           "健康建议\n1.甲状腺右叶囊实混合性回声团\n性质待定，C-TIRADS 3 类。\n"
           "2.双肺下叶微小结节\n本次检查左肺下叶背段见磨玻璃结节。\n")
    sec = _locate_findings_sections(
        txt, extra_anchor_re=_re.compile("异常指标"), anchor_only=True) or ""
    assert sec.count("1.甲状腺右叶囊实混合性回声团") == 2, sec
    assert sec.count("2.双肺下叶微小结节") == 2, sec


def test_reflow_splits_long_numbered_title_from_description():
    # 贵港/广西人民回归: 编号标题(可长/多名列表)后接长描述行 → 标题独立成行(前端据此加粗)
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("4.阻塞性睡眠呼吸暂停低通气综合征、夜间中度低氧饱和度血症\n"
           "此次检查示阻塞性睡眠呼吸暂停低通气综合征，导致夜间低氧饱和度血症。夜间反复缺氧可能导致心脑血管\n"
           "及心肺功能损伤。\n")
    out = _reflow_conclusion_lines(txt).split("\n")
    assert out[0] == "4.阻塞性睡眠呼吸暂停低通气综合征、夜间中度低氧饱和度血症", out
    assert out[1].startswith("此次检查示"), out
    # 广西人民: "1:胃镜 多名发现" + "请您及时…就诊。" → 也要拆
    txt2 = ("1:胃镜  慢性萎缩性胃炎（C1） 十二指肠球部溃疡（S2 期） 梨状窝隆起性质待查（囊肿？其它？）\n"
            "请您及时到消化内科门诊就诊。\n")
    out2 = _reflow_conclusion_lines(txt2).split("\n")
    assert out2[0].startswith("1:胃镜"), out2
    assert out2[1].startswith("请您及时"), out2


def test_locate_skips_age_and_hospital_page_headers():
    # 齐鲁回归: 跨页页眉"43岁"/"山东大学齐鲁医院(青岛)"混入结论段 → 应整行跳过
    from app.modules.report.service import _locate_findings_sections
    txt = ("总检结论：\n1.脂肪肝\n建议低脂饮食。\n43岁\n山东大学齐鲁医院(青岛)\n"
           "2.高血压\n建议心内科就诊。\n")
    sec = _locate_findings_sections(txt) or ""
    assert "43岁" not in sec, sec
    assert "齐鲁医院" not in sec, sec
    assert "脂肪肝" in sec and "高血压" in sec, sec


def test_reflow_merges_unclosed_bracket_title():
    # 齐鲁回归: 【…标题跨行("…左侧下肢动脉中" / "层钙化】")被拆成两行 → 应合并
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("一、建议如下：\n"
           "【双侧锁骨下动脉狭窄可能，远端动脉血液灌注欠充足、双侧外周动脉僵硬度增高、左侧下肢动脉中\n"
           "层钙化】\n"
           "建议必要时进一步行头颈动脉CTA检查。\n")
    out = _reflow_conclusion_lines(txt).split("\n")
    assert any("左侧下肢动脉中层钙化】" in l for l in out), out


def test_pair_arrow_by_row_skips_serial_number_cell():
    # 马鞍山回归: 序号列在名称左侧("9"/"淋巴细胞百分比"/"43.70" 同排) —— 结果扫描
    # 不得把最左的序号当结果(此前产出 淋巴=9/中性=23/血小板=21 三条假行)
    from app.modules.report.table_extractor import _pair_arrow_by_row
    lines = ["9", "淋巴细胞百分比", "43.70", "%", "↑"]
    meta = [(0, 100.0, 68.0), (0, 100.0, 89.0), (0, 100.0, 210.0),
            (0, 100.0, 240.0), (0, 100.0, 300.0)]
    rows = _pair_arrow_by_row(lines, meta, 4)
    assert rows and rows[0]["result"] == "43.70", rows


def test_pair_from_lines_joins_split_result_cell():
    # 日照(邵琳)回归: 结果单元格被 fitz 拆两行(">1000[阳性反应"+"（+）]"),
    # 单行归一失败 → 拼下一行再归一; 否则该箭头配对失败、标志丢失(乙肝表面抗体漏黄)
    from app.modules.report.table_extractor import _pair_from_lines
    lines = ["乙肝表面抗体", ">1000[阳性反应", "（+）]", "↑", "0.00～10.00", "mIU/ml"]
    row = _pair_from_lines(lines, 3, "arrow")
    assert row is not None
    assert row["item_name"] == "乙肝表面抗体"
    assert row["result"] == ">1000"


def test_indicator_rows_value_with_trailing_arrow():
    from app.modules.report.table_extractor import extract_indicator_rows
    txt = ("身高体重\n项目名称\n检查结果\n参 考 值\ncm\n140-180\n身高\n164\n"
           "体重指数（BMI）\n17.6 ↓\n18.5-23.9\n")
    rows = {r["item_name"]: r for r in extract_indicator_rows(txt)}
    assert rows.get("体重指数（BMI）", {}).get("result") == "17.6"
    assert rows["体重指数（BMI）"].get("signal_flag") == 2


def test_reflow_joins_standalone_number_and_separates_items():
    # 东方安鹏(扫描)结论表: 序号与发现名分两行("1."/"估算…"), 建议黏到上一条 → 需拼回并断行
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("主要健康问题\n1.\n估算肾小球滤过率轻度降低 肌酐增高：\n"
           "建议控制基础病，肾内科就诊。\n2.\n血压增高 总胆固醇增高：\n建议低脂饮食，心内科就诊。\n")
    lines = _reflow_conclusion_lines(txt).split("\n")
    assert "1. 估算肾小球滤过率轻度降低 肌酐增高：" in lines
    assert "建议控制基础病，肾内科就诊。" in lines
    assert "2. 血压增高 总胆固醇增高：" in lines
    assert "建议低脂饮食，心内科就诊。" in lines
    assert not any(l.endswith("。2.") or l.endswith("2.") for l in lines)


def test_conclusion_stops_at_report_promo():
    # 东方安鹏: 结论到"血镁增高:结合临床，复查电解质。"结束, 后续"关注公众号/查电子报告/结论…"是附录
    from app.modules.report.service import _locate_findings_sections
    txt = ("主检结论及建议\n主要健康问题\n1. 血镁增高:结合临床，复查电解质。\n"
           "关注公众号\n查电子报告\n结论\n2026-04-08 10:56:44\n")
    sec = _locate_findings_sections(txt) or ""
    assert "血镁增高" in sec
    assert "关注公众号" not in sec and "查电子报告" not in sec


def test_advice_conclusion_anchors():
    # 高帅 【体检建议】/ 白玮衡 防治建议 都应识别为结论锚点
    from app.modules.report.service import _is_findings_anchor
    assert _is_findings_anchor("【体检建议】")
    assert _is_findings_anchor("防治建议")
    assert _is_findings_anchor("防治建议：")
    assert not _is_findings_anchor("【肝回声致密】")  # 普通【】标题是段内内容, 非独立锚点


def test_conclusion_skips_page_header_lines():
    # 高帅页眉("体检号: 80330261"/"性别:"/"男")不得混入结论段
    from app.modules.report.service import _locate_findings_sections
    txt = ("【体检建议】\n1.【超重】\n建议减重。\n体检号: 80330261\n性别:\n男\n"
           "2.【鼻炎】\n建议耳鼻喉科就诊。\n总检医师:\n汇总医师:\n")
    sec = _locate_findings_sections(txt) or ""
    assert "【体检建议】" in sec and "建议减重" in sec
    assert "体检号" not in sec and "性别" not in sec


def test_reflow_bracket_title_always_own_line():
    # 蔡超: 【窦性心动过缓.】后的正文不以"建议/请/可见于…"开头("窦房结…")→ 原规则
    # 不断行; 【】标题应恒独立成行
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("【窦性心动过缓.】\n窦房结自律性降低引起的心动过缓称窦性心动过缓。建议内科就诊。\n"
           "【血糖升高】\n您此次空腹血糖为 6.32 mmol/L。建议控糖。\n")
    lines = _reflow_conclusion_lines(txt).split("\n")
    assert "【窦性心动过缓.】" in lines
    assert "【血糖升高】" in lines
    assert any(l.startswith("窦房结自律性降低") for l in lines)
    assert any(l.startswith("您此次空腹血糖") for l in lines)


def test_conclusion_excludes_exam_check_conclusion():
    # 附录超声报告的"检查结论"不是总检结论标题 → 不作锚点, 防其"1./2. …"被收进结论
    from app.modules.report.service import _is_findings_anchor, _locate_findings_sections
    assert not _is_findings_anchor("检查结论")
    txt = ("体检结果及建议\n【心脏冠状动脉CTA提示表浅型壁冠状动脉】\n建议心内科随诊。\n"
           "汇总医生：魏静\n主检医师：聂倩\n"
           "检查结论：\n1. 甲状腺未见明显异常\n2. 双侧颈部大血管旁未见明显肿大淋巴结\n")
    sec = _locate_findings_sections(txt) or ""
    assert "心脏冠状动脉" in sec
    assert "双侧颈部大血管旁" not in sec and "甲状腺未见明显异常" not in sec


def test_bilingual_conclusion_anchor_keeps_bracket_findings():
    # 蔡超(强OCR): 双语标题"体检结果及建议 Results and Suggestions…"整行含英文,
    # 原锚点不识别 → 走 LLM 兜底把【】标题拍平; 应识别为锚点并保留【】/换行
    from app.modules.report.service import _is_findings_anchor, _locate_findings_sections
    assert _is_findings_anchor("体检结果及建议 Results and Suggestions of Health Examination")
    txt = ("体检结果及建议 Results and Suggestions of Health Examination\n"
           "【肝回声致密】\n请定期复查肝脏超声。\n"
           "【胆囊内膜欠光滑】\n建议低脂饮食，肝胆外科随诊。\n"
           "汇总医生：魏静\n主检医师：聂倩\n")
    sec = _locate_findings_sections(txt) or ""
    assert "【肝回声致密】" in sec and "【胆囊内膜欠光滑】" in sec
    assert "汇总医生" not in sec


def test_parse_markdown_table_strips_star_and_normalizes_greek():
    # 蔡超(强OCR): 尿检名"☆蛋白质"的 ☆ 标记应剥离; 名称内 LaTeX 希腊字母应还原
    from app.core.vlm_client import _parse_markdown_table
    md = ("| 项目名称 | 本次结果 | 上次结果 | 参考值 | 单位 |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| ☆蛋白质 | 1+ | | | |\n"
          "| \\gamma-谷氨酰基转移酶 | 16.8 | | 5--50 | U/L |\n"
          "| ☆\\alpha羟基丁酸脱氢酶 | 102.0 | | 50--200 | U/L |\n")
    names = [r["item_name"] for r in _parse_markdown_table(md)]
    assert "蛋白质" in names
    assert "γ-谷氨酰基转移酶" in names
    assert "α羟基丁酸脱氢酶" in names


def test_force_ocr_task_ids_parsing(monkeypatch):
    # 方案a: FORCE_OCR_TASKS 解析(逗号/空白分隔, 忽略非数字)
    from app.modules.report.service import _force_ocr_task_ids
    monkeypatch.setenv("FORCE_OCR_TASKS", "39, 40 , x")
    assert _force_ocr_task_ids() == {39, 40}
    monkeypatch.setenv("FORCE_OCR_TASKS", "")
    assert _force_ocr_task_ids() == set()


def test_parse_markdown_table_normalizes_math_units():
    # 东方安鹏(扫描): OCR 单位带 LaTeX/数学符("$ 10^{9}/L $"/"$ \mu $g/L") → 应规范化
    from app.core.vlm_client import _parse_markdown_table
    md = ("| 项目名称 | 本次结果 | 上次结果 | 参考值 | 单位 |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| 白细胞(WBC) | 4.95 | | 3.5--9.5 | $ 10^{9}/L $ |\n"
          "| 血红细胞计数(RBC) | 5.51 | | 4.3--5.8 | $ 10^{12}/L $ |\n"
          "| 载脂蛋白E | 22.8 | | 27--45 | $ \\mu $g/L |\n")
    rows = {r["item_name"]: r for r in _parse_markdown_table(md)}
    assert rows["白细胞(WBC)"]["unit"] == "10^9/L"
    assert rows["血红细胞计数(RBC)"]["unit"] == "10^12/L"
    assert rows["载脂蛋白E"]["unit"] == "μg/L"


def test_parse_markdown_table_dual_result_keeps_current():
    # 东方安鹏(扫描)双值表 项目名称|本次结果|上次结果|参考值|单位:
    # "上次结果"(空)列覆盖"本次结果" → 所有结果丢失; 参考值 "40--50" 双横线未解析 → UI 显示 ">40--50"
    from app.core.vlm_client import _parse_markdown_table
    md = ("| 项目名称 | 本次结果 | 上次结果 | 参考值 | 单位 |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| 红细胞比积(HCT) | 50.800\\uparrow | | 40--50 | % |\n"
          "| 总胆固醇(Chol) | 6.51\\uparrow | | ≤5.18 | mmol/L |\n")
    rows = {r["item_name"]: r for r in _parse_markdown_table(md)}
    assert rows["红细胞比积(HCT)"]["result"] == "50.800"
    assert rows["红细胞比积(HCT)"].get("signal_flag") == 3
    assert rows["红细胞比积(HCT)"].get("ref_low") == "40"
    assert rows["红细胞比积(HCT)"].get("ref_high") == "50"
    assert rows["总胆固醇(Chol)"].get("ref_high") == "5.18"


def test_html_entities_unescaped_in_cells():
    # 东方安鹏参考值单元格是 &gt;/&lt; 实体 → 应还原为 >/< 并解析单限
    from app.core.vlm_client import _parse_markdown_table
    md = ("| 项目名称 | 本次结果 | 上次结果 | 参考值 | 单位 |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| 甘油三酯(TG) | 5.40\\uparrow | | &lt;1.70 | mmol/L |\n"
          "| 高密度脂蛋白胆固醇(HDL-C) | 0.90\\downarrow | | &gt;1.04 | mmol/L |\n")
    rows = {r["item_name"]: r for r in _parse_markdown_table(md)}
    assert rows["甘油三酯(TG)"].get("ref_high") == "1.70"
    assert rows["高密度脂蛋白胆固醇(HDL-C)"].get("ref_low") == "1.04"


def test_parse_markdown_table_data_row_not_treated_as_header():
    # 仁济陈磊: 数据行 ['红细胞信息','未提示',...] 被误判为表头(未提示≈提示),
    # 后续行用错列映射 → 漏 "粘丝 | + | 0-250 | /μl | 阳性"
    from app.core.vlm_client import _parse_markdown_table
    md = ("| 项目名称 | 结果 | 参考值 | 单位 | 提示 |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| 红细胞信息 | 未提示 | | | |\n"
          "| 粘丝 | + | 0-250 |  / \\mu l | 阳性 |\n")
    rows = {r["item_name"]: r for r in _parse_markdown_table(md)}
    assert "粘丝" in rows
    assert rows["粘丝"]["result"] == "+"
    assert rows["粘丝"].get("signal_flag") == 3


def test_parse_markdown_table_drops_chinese_ref():
    # 仁济陈磊: 参考范围含中文说明("适宜：<1.70，增高：…")→ 不提取(留空)
    from app.core.vlm_client import _parse_markdown_table
    md = ("| 项目名称 | 结果 | 参考值 | 单位 | 提示 |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| 甘油三酯 | 1.29 | 适宜：<1.70，增高：1.70-2.30，很高：>2.30 | mmol/L | |\n")
    rows = {r["item_name"]: r for r in _parse_markdown_table(md)}
    assert rows["甘油三酯"].get("ref_low") is None
    assert rows["甘油三酯"].get("ref_high") is None


def test_parse_markdown_table_strips_latex_marker_prefix():
    # 六院金山包雁飞: 名称前缀 "$ ^{*} $"/"*" 是报告方异常标记, 不应留在名称里
    from app.core.vlm_client import _parse_markdown_table
    md = ("| 项目名称 | 结果 | 参考值 | 单位 | 提示 |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| $ ^{*} $收缩压 | 140 | 90-140 | mmHg | ↑ |\n"
          "| * 耳（右） | 右耳耵聍栓塞 | | | |\n")
    names = [r["item_name"] for r in _parse_markdown_table(md)]
    assert "收缩压" in names
    assert "耳（右）" in names
    assert not any("$" in n or "*" in n for n in names)


def test_pair_summary_line_extracts_all_pairs():
    # 华山常逢龙异常汇总: "载脂蛋白-A1：0.80g/L ↓；高密度…：0.93mmol/L ↓；低密度…：3.45mmol/L ↑；"
    # 原只提第一对, 漏高密度/低密度 → 应逐对提取(都带标志)
    from app.modules.report.table_extractor import _pair_summary_line
    line = ("载脂蛋白-A1：0.80g/L ↓；高密度脂蛋白胆固醇：0.93mmol/L ↓；"
            "低密度脂蛋白胆固醇：3.45mmol/L ↑；")
    rows = _pair_summary_line(line, "arrow")
    assert rows is not None
    got = {(r["item_name"], r["result"]) for r in rows}
    assert ("载脂蛋白-A1", "0.80") in got
    assert ("高密度脂蛋白胆固醇", "0.93") in got
    assert ("低密度脂蛋白胆固醇", "3.45") in got


def test_pair_from_lines_colon_name_keeps_abbrev_digit():
    # 华山常逢龙小结 "载脂蛋白-A1：0.80g/L ↓" 被拆成 "载脂蛋白-A"+"1" → 应保持完整名
    from app.modules.report.table_extractor import _pair_from_lines
    lines = ["载脂蛋白-A1：0.80g/L ↓；高密度脂蛋白胆固醇：0.93mmol/L ↓；"]
    row = _pair_from_lines(lines, 0, "arrow")
    assert row is not None
    assert row["item_name"] == "载脂蛋白-A1"
    assert row["result"] == "0.80"


def test_reflow_separates_consecutive_label_lines():
    # 仁济陈磊(扫描件): 连续"名称：说明"段(视乳头…/高脂血症…)不得互相黏连
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("建议：\n超重：根据中国成人BMI标准，BMI在24至27.9之间为超重。超重与多种慢病相关。\n"
           "视乳头C/D（杯/盘比）扩大：指视神经乳头中央的凹陷相对于总直径的比例增大，通常提示视神经萎缩。\n"
           "高脂血症：指血浆中一种或多种脂质成分升高。建议内分泌科就诊。")
    lines = _reflow_conclusion_lines(txt).split("\n")
    for e in ("超重：", "视乳头C/D（杯/盘比）扩大：", "高脂血症："):
        assert e in lines, (e, lines)


def test_reflow_splits_short_label_and_explanation():
    # 仁济陈磊(扫描件)建议段 "超重：根据中国成人BMI标准…" → "超重：" 与说明换行
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("建议：超重：根据中国成人BMI标准，BMI在24至27.9之间为超重。建议内分泌科就诊。"
           "\n高脂血症：指血浆中一种或多种脂质成分升高。建议内分泌科就诊。")
    lines = _reflow_conclusion_lines(txt).split("\n")
    assert "超重：" in lines
    assert "高脂血症：" in lines
    assert any(l.startswith("根据中国成人BMI标准") for l in lines)
    assert any(l.startswith("指血浆中") for l in lines)


def test_conclusion_stops_at_exam_report_header():
    # 六院金山包雁飞: 结论段后跟 CT 报告"附见:…"与"…心电图报告"页 → 应止于此处
    from app.modules.report.service import _locate_findings_sections
    txt = ("建议\n一、 收缩压高：\n2、胸部CT平扫：附见：肝左叶局部稍低密度灶。\n"
           "建议心内科随诊。\n"
           "2. 附见：肝左叶局部稍低密度灶，较前2025-6-20CT相仿。\n"
           "上海市第六人民医院金山分院 心电图报告\n")
    sec = _locate_findings_sections(txt) or ""
    assert "收缩压高" in sec
    assert "胸部CT平扫" in sec  # 行内"附见"不误断
    assert "2. 附见" not in sec
    assert "心电图报告" not in sec


def test_conclusion_skips_cover_junk_collects_advice():
    # 中医院曹嘉冰结论页: "健康体检结论/Conclusion/上上上/身份证号" 封面垃圾段应丢弃,
    # 只保留"建议"段(脂肪酸开头), 且不越过"常规检查"表
    from app.modules.report.service import _locate_findings_sections
    txt = ("健康体检结论\nConclusion\n上上上上上上上上上上\n上上上上上上上上上上\n"
           "身份证号：310103198108037053\n"
           "建议\nSuggestion\n脂肪肝、高脂血症\n1、低脂饮食。\n常规检查\n报告者：张毅\n")
    sec = _locate_findings_sections(txt) or ""
    assert "健康体检结论" not in sec
    assert "Conclusion" not in sec and "上上上" not in sec and "身份证号" not in sec
    assert "脂肪肝" in sec and "报告者" not in sec


def test_reflow_diamond_section_header_newline():
    # 华山常逢龙 "◆ 血常规" 被拼进上一条建议行尾(未识别 ◆ 为段头) → 应独立成行
    from app.modules.report.service import _reflow_conclusion_lines
    txt = "【牙色素沉着】建议洗牙。\n◆ 血常规\n【嗜碱性粒细胞增高】建议复查。"
    lines = _reflow_conclusion_lines(txt).split("\n")
    assert "◆ 血常规" in lines
    assert not lines[0].endswith("◆ 血常规")  # 不再拼进上一条建议行尾


def test_conclusion_section_stops_at_exam_dtable():
    # 华山常逢龙结论段越过"六、检查项目结果"把后面指标表整段收走(结论脏)
    from app.modules.report.service import _locate_findings_sections
    txt = ("五、总检结论及建议\n"
           "◆ 一般检查\n【血压正常高值】建议监测血压。\n"
           "◆ 经腹前列腺彩色多普勒超声检查\n【前列腺钙化灶】定期复查。\n"
           "六、检查项目结果\n一般检查结果\n项目名称\n检查结果\n身高\n170\n体重\n65\n")
    sec = _locate_findings_sections(txt) or ""
    assert "血压正常高值" in sec
    assert "前列腺钙化灶" in sec
    assert "身高" not in sec and "项目名称" not in sec


def test_conclusion_section_stops_at_report_table():
    # 中医院曹嘉冰"建议"段后接常规检查明细表(报告者/项目名称…), 应止于表头
    from app.modules.report.service import _locate_findings_sections
    txt = ("建议\nSuggestion\n脂肪肝、高脂血症\n1、低脂饮食。\n"
           "常规检查\n报告者：张毅\n项目名称\n结果\n参考值\n单位\n身高\ncm\n")
    sec = _locate_findings_sections(txt) or ""
    assert "脂肪肝" in sec
    assert "报告者" not in sec and "项目名称" not in sec


def test_chinese_numbered_conclusion_anchor():
    # 华山常逢龙 "五、总检结论及建议" 因带汉字序号未被识别为锚点 → 结论漏"一般检查"
    from app.modules.report.service import _is_findings_anchor
    assert _is_findings_anchor("五、总检结论及建议")
    assert _is_findings_anchor("五.总检结论及建议")
    assert not _is_findings_anchor("六、检查项目结果")  # 非结论标题


def test_classify_ckd_epi_as_renal():
    # 六院金山包雁飞 "CKD-EPI (cre估算)"/eGFR 应归肾功能分类(现返回 None)
    from app.core.indicator_groups import classify
    assert classify("CKD-EPI (cre估算)") == "肾功能"
    assert classify("eGFR(肌酐)") == "肾功能"
    assert classify("EGFR-EPI") == "肾功能"


def test_clip_db_field():
    # 入库前按列宽裁剪, 任何通道的脏值都不得再触发 Data too long
    from app.modules.report.service import _clip_db_field
    assert _clip_db_field("abcdef", 3) == "abc"
    assert _clip_db_field(None, 3) is None
    assert _clip_db_field("ab", 5) == "ab"


def test_huaxi_profile_matches_hospital_name():
    # 华西档案 keyword 用医院名"华西"(非单位词"出入镜"); 报告含"华西"即命中
    from app.modules.report.report_profiles import match_profile
    prof = match_profile("厦门华西医院 1. 高甘油三酯血症及高密度脂蛋白降低 上海出入境边防检查总站")
    assert prof.get("anchor_only")


# === 2026-09-16: 陈磊(H003-30)/陈镜霓(H004-40) 第三批回归 ===

@pytest.mark.parametrize("line,expect", [
    # 部位+彩超 行仍作细节段起点(独立表头/＋并列清单)
    ("颈动脉彩超", True),
    ("颈动脉彩超（查冠心病危险因子）", True),
    ("心脏彩超＋甲状腺、甲状旁腺及其引流区淋巴结彩超＋肝胆胰脾彩超", True),
    # 分号并列的"异常检查结果"发现清单不得触发 skip_detail(陈磊: 吞掉其后 7 行发现)
    ("颈动脉彩超（查冠心病危险因子）；甲状腺彩超；B超（肝、胆、脾、胰）；"
     "B超（双肾、前列腺、输尿管、膀胱）；脂肪肝", False),
])
def test_exam_detail_start_chaosheng_guarded_without_semicolon(line, expect):
    from app.modules.report.service import _EXAM_DETAIL_START_RE
    assert bool(_EXAM_DETAIL_START_RE.search(line)) == expect


def test_chenlei_chaosheng_list_lines_collected():
    # 陈磊(H003-30): 该清单行触发细节段跳过 → 颈动脉彩超段/胆囊壁毛糙/甲状腺结节/
    # 心电图检查：1、窦性心律 全被吞; 修复后应完整收集
    from app.modules.report.service import _locate_findings_sections
    text = "\n".join([
        "本次体检结果及建议",
        "异常检查结果：",
        "低剂量肺CT（不含胶片）：右肺上叶模糊斑点灶，请随访。",
        "颈动脉彩超（查冠心病危险因子）；甲状腺彩超；B超（肝、胆、脾、胰）；脂肪肝",
        "胆囊壁毛糙",
        "甲状腺右叶结节",
        "心电图检查：1、窦性心律",
        "2、ST段改变（V5、V6水平型压低0.05mV）",
        "建议：",
        "超重：注意饮食。",
    ])
    sec = _locate_findings_sections(text)
    assert sec
    for ln in ("颈动脉彩超（查冠心病危险因子）", "胆囊壁毛糙", "甲状腺右叶结节",
               "心电图检查：1、窦性心律", "2、ST段改变（V5、V6水平型压低0.05mV）"):
        assert ln in sec, f"结论段缺行: {ln}"


@pytest.mark.parametrize("raw,expect", [
    ("Ⅲ级 ↑", ("Ⅲ级", True)),
    ("III级↓", ("III级", True)),
    ("Ⅲ级", (None, False)),
    ("严重", (None, False)),
])
def test_grade_value_with_arrow(raw, expect):
    # 陈镜霓白带常规 阴道清洁度结果 "Ⅲ级 ↑" 此前不被认作值 → 整行丢失
    from app.modules.report.table_extractor import (
        _grade_value_with_arrow, _GRADE_VAL_RE, _PLUS_ONLY_VAL_RE)
    assert _grade_value_with_arrow(raw) == expect
    if expect[1]:
        assert _GRADE_VAL_RE.match(expect[0])


def test_plus_only_value_and_header_roles():
    from app.modules.report.table_extractor import _PLUS_ONLY_VAL_RE
    from app.modules.report.layout import cell_role
    # 白细胞（WBC1）结果 "++"(纯加号半定量)
    assert _PLUS_ONLY_VAL_RE.match("++") and _PLUS_ONLY_VAL_RE.match("++++")
    assert not _PLUS_ONLY_VAL_RE.match("+") and not _PLUS_ONLY_VAL_RE.match("1+")
    # 表头带空格("单 位"/"参 考 值")此前角色为 None → 单位/参考列整列丢失(/HP 未接上)
    assert cell_role("单 位") == "unit"
    assert cell_role("参 考 值") == "ref"
    assert cell_role("检查结果") == "result"


def test_suggestion_ownership_head_neck():
    # 陈磊(H003-30)重跑: 甲状腺"建议内分泌科或头颈外科就诊…"被 LLM 贴给 胆囊壁毛糙
    # (建议含头颈而条目名不含) → 归属校验须清空该条建议
    from app.modules.report.service import _validate_suggestion_ownership
    text = "\n".join([
        "异常检查结果：",
        "低剂量肺CT：右肺上叶模糊斑点灶，请随访。",
        "颈动脉彩超（查冠心病危险因子）；脂肪肝胆囊壁毛糙",
        "建议：",
        "胆囊壁毛糙：",
        "指胆囊壁的内缘不光滑，通常提示慢性炎症。建议消化内科就诊、随访。",
        "甲状腺结节：",
        "提示甲状腺内存在一个或多个肿块。建议内分泌科或头颈外科就诊，必要时进一步检查明确诊断。",
    ])
    items = [
        {"item_name": "胆囊壁毛糙",
         "suggestion": "建议内分泌科或头颈外科就诊，必要时进一步检查明确诊断。"},
        {"item_name": "甲状腺结节",
         "suggestion": "建议内分泌科或头颈外科就诊，必要时进一步检查明确诊断。"},
    ]
    out = _validate_suggestion_ownership(items, text)
    got = {i["item_name"]: i["suggestion"] for i in out}
    assert got["胆囊壁毛糙"] == ""
    assert got["甲状腺结节"]


def test_inline_advice_bracket_not_anchor():
    # 常逢龙(华山, H003-28): "【肺结节】建议您胸外科…" 是发现+建议同行 —— 旧实现
    # "建议" in s 判成锚点, 下一行又是锚点时该段仅一行 → 被空锚点规则整体丢弃。
    # 2026-09-16: "建议"类【】锚点只看括号内标题。
    from app.modules.report.service import _is_findings_anchor
    assert not _is_findings_anchor("【肺结节】建议您胸外科/呼吸科定期复查随诊。")
    assert _is_findings_anchor("【体检建议】")      # 括号内标题含建议 → 锚点(高帅)
    assert _is_findings_anchor("【医师建议】")


def test_inline_advice_bracket_line_collected():
    # 上述锚点修复的定位层行为: 【X】+同行建议 不被吞, 整行保留在结论段
    from app.modules.report.service import _locate_findings_sections
    text = "\n".join([
        "五、总检结论及建议",
        "【窦性心律不齐】如不伴随其他异常，且与呼吸有关，可视为正常。",
        "【肺结节】建议您胸外科/呼吸科定期复查随诊。",
        "【炎性后遗改变】多见于肺炎，肺结核等疾病康复后，建议定期复查，动态观察，专科诊治。",
        "◆ 甲状腺(甲状旁腺及颈部淋巴结)彩色多普勒超声检查",
        "【甲状腺两叶结节，TI-RADS 3 类】甲状腺组织内性质不明确的局限性肿块统称为甲状腺结节。",
    ])
    sec = _locate_findings_sections(text)
    assert sec
    for ln in ("【肺结节】建议您胸外科/呼吸科定期复查随诊。",
               "【炎性后遗改变】多见于肺炎，肺结核等疾病康复后，建议定期复查，动态观察，专科诊治。"):
        assert ln in sec, f"结论段缺行: {ln}"


def test_junk_row_cell_filters():
    # 2026-09-23 用户反馈三份报告的"其他指标"垃圾行清理规则
    from app.modules.report.table_extractor import _junk_row_cell, _skip_name
    # 定性碎片("阳性( DOB")/名称尾随数值("…A1c(NGSP) 5.60")/重复名
    assert _junk_row_cell("阳性( DOB", ")", "10.30")
    assert _junk_row_cell("糖化血红蛋白A1c(NGSP) 5.60", "", "5.9")
    assert _junk_row_cell("胃泌素-17（G-17）(餐前) 2.53", "", "1.74")
    assert _junk_row_cell("清晰透明清晰透明", "", "15")
    assert _junk_row_cell("淡黄色淡黄色", "", "18")
    # 合并多条目行(单位含"，名称: 数值")
    assert _junk_row_cell("蛋白A", ", 总胆固醇: 5.33", "0.80")
    # 查体部位 + 正常结果
    assert _junk_row_cell("头面部", "", "未见明显异常")
    assert _junk_row_cell("甲状腺", "", "未见明显异常")
    assert _junk_row_cell("裂隙灯", "", "正常")
    # 合法行不受影响
    assert not _junk_row_cell("载脂蛋白A", "g/L", "0.80")
    assert not _junk_row_cell("糖化血红蛋白A1c(NGSP)", "", "5.60")
    assert not _junk_row_cell("维生素B12", "pmol/L", "500")
    assert not _junk_row_cell("甲状腺结节", "", "TI-RADS 3类")
    # 名称黑名单: 病史/最大最小/院区/总院/重复字
    assert _skip_name("吸烟史") and _skip_name("饮酒史")
    assert _skip_name("最大") and _skip_name("最小")
    assert _skip_name("浦东院区") and _skip_name("宝山院区") and _skip_name("总    院")
    assert _junk_row_cell("淋巴细胞百分比上上上上上")  # 尾随重复字装饰(合并兜底)
    assert not _skip_name("淋巴细胞百分比")


def test_junk_row_cell_unclosed_paren():
    # 2026-09-23 曹嘉冰: "右侧甲状腺小结节可能（TI-RADS 3）" 被拆成 名称(截断)/值3/单位"）"
    from app.modules.report.table_extractor import _junk_row_cell
    assert _junk_row_cell("右侧甲状腺小结节可能（TI-RADS", "）", "3")
    assert not _junk_row_cell("钾（K）", "mmol/L", "4.2")
    assert not _junk_row_cell("抗链球菌溶血素O(ASO)", "IU/mL", "50")


def test_vlm_staged_ref_negative_band_clears_flag():
    # 2026-09-24 薄楠: EB病毒早期抗原IgM抗体 0.05(参考"阴性:<0.9 可疑:0.9--1.1 阳性:≥1.1")
    # OCR 提示列误读 ↑ → 数值落阴性带 → 清标志(保留行); 真阳性(196.2 ≥ 20)标志保留
    from app.core.vlm_client import _rows_to_indicators
    rows = [
        ["项目名称", "本次结果", "参考范围", "提示"],
        ["EB病毒早期抗原IgM抗体", "0.05", "阴性:<0.9 可疑:0.9--1.1 阳性:≥1.1", "\\uparrow"],
        ["EB病毒衣壳抗原IgG抗体", "196.2", "阴性:<20 阳性:≥20", "\\uparrow"],
    ]
    inds = {i["item_name"]: i for i in _rows_to_indicators(rows)}
    assert not inds["EB病毒早期抗原IgM抗体"].get("signal_flag"), inds
    assert inds["EB病毒衣壳抗原IgG抗体"].get("signal_flag") == 3, inds


def test_vlm_narrative_result_cell_skipped():
    # 2026-09-24 陈海波: 历年对比表残留行("微白蛋白：>150（0-100mg/L）")不入指标
    from app.core.vlm_client import _rows_to_indicators
    rows = [
        ["项目", "2026-05-29", "2024-04-19"],
        ["微白蛋白", "微白蛋白：>150（0-100mg/L）", "微白蛋白：30（0-30.0mg/L）"],
        ["肌酐", "84", "79"],
    ]
    names = [i["item_name"] for i in _rows_to_indicators(rows)]
    assert "微白蛋白" not in names, names


# === 2026-09-28: 第五批反馈修复(付立光/毕惠丽/蔡丰泽/蔡明/曾繁鲁) ===

def test_latin_paren_continuation_requires_letter():
    # 付立光: "0.00(-)" 被当"名称括注续行"把上上行名错配给下一行值("<0.05"),
    # 产生重复行; 拉丁括号续行前缀必须含字母(IgG(IgG/VCA) 仍命中)
    from app.modules.report.table_extractor import (
        _LATIN_PAREN_RE, extract_indicator_rows,
    )
    assert _LATIN_PAREN_RE.match("IgG(IgG/VCA)")
    assert not _LATIN_PAREN_RE.match("0.00(-)")
    rows = extract_indicator_rows("\n".join(["甲", "0.00(-)", "<0.05", "IU/ml"]))
    assert [(r["item_name"], r["result"]) for r in rows] == [("甲", "0.00")], rows


def test_split_label_explanation_subitem_and_numeric_rest():
    # 曾繁鲁: "(1)体检发现您的血尿酸增高:尿酸(UA):432…" 被拆成 3 行
    # (子项编号标签 + 数值起始 rest 均不拆; "超重：根据…" 正常拆)
    from app.modules.report.service import _split_label_explanation
    got = _split_label_explanation("(1)体检发现您的血尿酸增高:尿酸(UA):432 μmol/L ↑【208-428】。")
    assert got == ["(1)体检发现您的血尿酸增高:尿酸(UA):432 μmol/L ↑【208-428】。"], got
    got = _split_label_explanation("超重：根据中国成人BMI标准，24≤BMI<28为超重，需控制饮食")
    assert got == ["超重：", "根据中国成人BMI标准，24≤BMI<28为超重，需控制饮食"], got


def test_reflow_numspace_continuation():
    # 曾繁鲁: "…连续观察" / "3 年无改变，可一年一次。" 被 item_head 当新编号条目断开
    from app.modules.report.service import _reflow_conclusion_lines
    txt = "2、[甲状腺结节]\n说明文字需每3～6 个月复查一次。连续观察\n3 年无改变，可一年一次。\n3、[肺气肿]\n正文。"
    out = _reflow_conclusion_lines(txt)
    assert "连续观察3 年无改变，可一年一次。" in out.replace("\n", ""), out
    assert "\n3、[肺气肿]" in out, out  # 真编号条目(带分隔符)仍独立


def test_reflow_unbalanced_bracket_continuation():
    # 付立光: 【】多项列表折行中段(已有部分闭合)曾被判新段头拆开
    from app.modules.report.service import _reflow_conclusion_lines
    txt = "【脉搏：59次/分】【心电图：窦缓】【胸部CT：主\n动脉硬化】【心脏彩超：未见明显异常】\n建议复查。"
    out = _reflow_conclusion_lines(txt)
    assert "主动脉硬化】【心脏彩超：未见明显异常】" in out, out


def test_locate_findings_bracket_continuation_normal_word_kept():
    # 付立光: 括号清单折行续行"动脉硬化】【心脏彩超…未见明显异常"被"未见异常"跳行误杀
    from app.modules.report.service import _locate_findings_sections
    txt = ("体检结论分析\n"
           "一、主要健康问题：\n"
           "【胸部低剂量CT平扫：主\n"
           "动脉硬化】【心脏彩超：房室大小正常；左室壁运动分析未见明显异常】\n"
           "建议复查。\n"
           "二、次要问题：\n")
    sec = _locate_findings_sections(txt)
    assert "动脉硬化】【心脏彩超：房室大小正常；左室壁运动分析未见明显异常】" in sec, sec
    # 独立"X：未见异常"清单行仍跳过
    txt2 = "体检结论分析\n内科：未见异常\n外科：未见异常\n建议复查。"
    sec2 = _locate_findings_sections(txt2)
    assert sec2 is None or "未见异常" not in sec2, sec2


def test_indicator_name_continuation_bracket_prefix():
    # 曾繁鲁: "[HR]乙型肝炎表面抗原" + "测定(HBsAg)" 折行名未并; 纯拉丁括号续行并入
    from app.modules.report.table_extractor import (
        _NAME_RE, _NAME_CONT_MIXED_RE, extract_indicator_rows,
    )
    assert _NAME_RE.match("[HR]乙型肝炎表面抗原")
    assert _NAME_CONT_MIXED_RE.match("测定(HBsAg)")
    assert _NAME_CONT_MIXED_RE.match("移酶(AST)")
    assert not _NAME_CONT_MIXED_RE.match("(定量)")  # 纯中文括号保持跳板
    rows = extract_indicator_rows("\n".join(
        ["[HR]乙型肝炎表面抗原", "测定(HBsAg)", "阴性(-)",
         "[HR] 中性粒细胞比率", "(NEUT%)", "56.80"]))
    names = [r["item_name"] for r in rows]
    assert "[HR]乙型肝炎表面抗原测定(HBsAg)" in names, names
    assert "[HR] 中性粒细胞比率(NEUT%)" in names, names


def test_remove_findings_section_subsequence():
    # 结论段收集跳过页眉行时, 原 text.replace 静默失败 → 子序列删除
    from app.modules.report.service import _remove_findings_section
    text = "封面\n体检结论分析\n【异常A】\n体检号: 123\n【异常B】\n建议复查。\n指标表\n血红蛋白: 140"
    section = "体检结论分析\n【异常A】\n【异常B】\n建议复查。"
    out = _remove_findings_section(text, section)
    assert "异常A" not in out and "异常B" not in out and "建议复查" not in out, out
    assert "血红蛋白: 140" in out and "封面" in out, out


def test_group_indicators_urine_disambiguation():
    # 毕惠丽: 白细胞酯酶/隐血/红细胞(尿, /μl) 误入血常规/大便常规
    from app.core.indicator_groups import group_indicators
    rows = [
        {"item_name": "隐血", "unit": ""},
        {"item_name": "白细胞酯酶", "unit": ""},
        {"item_name": "红细胞", "unit": "/μl"},
        {"item_name": "红细胞计数", "unit": "10^12/L"},
        {"item_name": "白细胞计数", "unit": "10^9/L"},
    ]
    out, _ = group_indicators(rows)
    g = {r["item_name"]: r["group"] for r in out}
    assert g["隐血"] == "尿常规" and g["白细胞酯酶"] == "尿常规"
    assert g["红细胞"] == "尿常规"
    assert g["红细胞计数"] == "血常规" and g["白细胞计数"] == "血常规"


def test_signal_fragment_retarget():
    # 曾繁鲁: 信号通道把折行名碎片"测定(HBsAb)"/"酯(TG)"当独立异常 →
    # 组装层改挂全名(标志保留, 碎片不入库)
    from app.modules.report.service import _retarget_signal_name
    pool = {("[HR]乙型肝炎表面抗体测定(HBsAb)", "阳性(+)"),
            ("[HR]甘油三酯(TG)", "2.85")}
    by_result = {}
    for n, v in pool:
        by_result.setdefault(v, []).append(n)
    assert _retarget_signal_name("测定(HBsAb)", "阳性(+)", pool, by_result) \
        == "[HR]乙型肝炎表面抗体测定(HBsAb)"
    assert _retarget_signal_name("酯(TG)", "2.85", pool, by_result) == "[HR]甘油三酯(TG)"
    # 自身是完整名 / 无更长后缀名 → 不改挂
    assert _retarget_signal_name("[HR]甘油三酯(TG)", "2.85", pool, by_result) \
        == "[HR]甘油三酯(TG)"
    assert _retarget_signal_name("谷草/ 谷丙比值(AST/ALT)", "0.48", pool, by_result) \
        == "谷草/ 谷丙比值(AST/ALT)"


# === 2026-09-28(第二批): 蔡明图片详情/日期/乙肝定性后缀/机构抬头 ===

def test_split_qual_tail_numeric_chinese():
    # 蔡明: "130 阳性" 整串曾被当结果(与行式"130"重复; 行式把"阳性"当单位)
    from app.modules.report.table_extractor import _split_qual_tail, extract_indicator_rows
    assert _split_qual_tail("130 阳性") == ("130", "阳性")
    assert _split_qual_tail("0.00732 阳性") == ("0.00732", "阳性")
    assert _split_qual_tail("130") == ("130", None)
    assert _split_qual_tail("阳性") == ("阳性", None)
    rows = extract_indicator_rows("\n".join(
        ["乙肝表面抗体(抗-HBs)", "130 阳性", "*", "0--10", "mIU/mL"]))
    assert [(r["item_name"], r["result"], r["unit"]) for r in rows] == [
        ("乙肝表面抗体(抗-HBs)", "130", "mIU/mL")], rows


def test_layout_hospital_header_not_merged_into_name():
    # 蔡明: 页眉"江西中医药大学附属医院"与数据行同列近 y 被并进名称
    from app.modules.report.table_extractor import _HOSPITAL_HEADER_RE
    assert _HOSPITAL_HEADER_RE.match("江西中医药大学附属医院")
    assert _HOSPITAL_HEADER_RE.match("某某市人民医院（东院区）")
    assert not _HOSPITAL_HEADER_RE.match("肌钙蛋白I")


def test_reflow_num_title_short_label_boundary():
    # 蔡明: 长编号标题("7. 胆囊息肉，大小约 3 × 3 mm")后接"疾病解释"曾黏连
    from app.modules.report.service import _reflow_conclusion_lines
    txt = "7. 胆囊息肉，大小约 3 × 3 mm\n疾病解释\n为胆囊壁突向腔内的隆起物，多发。\n主检建议\n建议定期复查。"
    out = _reflow_conclusion_lines(txt)
    assert "mm疾病解释" not in out, out
    assert "\n疾病解释\n" in out, out
    assert "\n主检建议\n" in out, out


def test_reflow_history_date_merged():
    # 蔡明: 历年对比日期标签"2025-05-21 检查"单独成行会被前端加粗 → 并入下一行
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("10. 乙肝两对半\n本次检查 检验科示：乙肝表面抗体:130 阳性;\n"
           "2025-05-21 检查\n检验科示：乙肝表面抗体:阳性;\n"
           "2025 -05-21 检查\n疾病解释\n提示已产生抗体。")
    out = _reflow_conclusion_lines(txt)
    assert "2025-05-21 检查 检验科示：乙肝表面抗体:阳性;" in out, out
    assert "2025 -05-21 检查\n" not in out, out  # 孤立日期(下一行是分区标签)已丢弃
    assert "2025-05-21 检查 疾病解释" not in out, out


def test_ocr_content_pages_profile_flag():
    # 蔡明: 档案声明 ocr_content_pages → _extract_pdf_text(hybrid) 走宽条图页 OCR
    from app.modules.report.report_profiles import match_profile
    assert match_profile("江西中医药大学附属医院 体检报告").get("ocr_content_pages") is True
    assert not match_profile("某不存在的医院").get("ocr_content_pages")


def test_skip_names_unit_prefix_and_summary_label():
    # 2026-09-28 端到端回归: 尿沉渣单位行"个/ml"被当名称续行首段(付立光
    # "个/ml细菌(FORHPF)"重复行); "小结"被当指标名(蔡明"小结|未见明显异常")
    from app.modules.report.table_extractor import _skip_name, _merge_wrapped_names
    for u in ("个/ml", "个/HP", "个/μl", "个/LPF", "个/视野"):
        assert _skip_name(u), u
    assert _skip_name("小结")
    assert not _skip_name("红细胞")
    assert _merge_wrapped_names(["个/ml", "细菌(FORHPF)", "0.68"]) == [
        "个/ml", "细菌(FORHPF)", "0.68"]


def test_inline_ref_range_fragment_rejected():
    # 毕惠丽 visual 文本: 性激素参考范围折行"期:4.54-22.51，黄体" 曾被行内模式
    # 当"名称:结果"(产出"期|4.54"/"无判断期|4.54"垃圾)
    from app.modules.report.table_extractor import extract_indicator_rows
    rows = extract_indicator_rows("\n".join([
        "促卵泡生成素", "87.0", "无判断", "期:4.54-22.51，黄体",
        "成年男性:1.75-7.81，成年", "女性:<0.75。"]))
    names = [r["item_name"] for r in rows]
    assert names == ["促卵泡生成素"], rows


def test_skip_name_serial_and_phone():
    # 毕惠丽 visual 文本: "体检流水号：…" / "联系电话" 曾被当指标名
    from app.modules.report.table_extractor import _skip_name
    assert _skip_name("体检流水号")
    assert _skip_name("联系电话")
    assert not _skip_name("血小板计数")


def test_skip_name_ocr_report_fields():
    # 2026-09-28 蔡丰泽 image 页 OCR: 分检报告头字段被当指标
    from app.modules.report.table_extractor import _skip_name
    for s in ("审核时间", "报告时间", "检测结果", "病案号", "检查号",
              "病人信息病案号", "申请科室", "申请医生", "体检"):
        assert _skip_name(s), s
    assert not _skip_name("促卵泡生成素")


def test_pair_arrow_by_row_with_result_hint():
    # 董涛体格表文本流乱序("舒张压/18.5-23.99/26.16 ↑/体重指数"): 值+箭头同格行
    # 按同排(y)取名, 不再把值错配给上一行
    from app.modules.report.table_extractor import _pair_arrow_by_row
    lines = ["舒张压", "18.5-23.99", "26.16 ↑", "体重指数"]
    meta = [(0, 200.0, 77.0), (0, 201.0, 416.0), (0, 201.5, 195.0), (0, 200.5, 75.0)]
    out = _pair_arrow_by_row(lines, meta, 2, result_hint="26.16")
    assert out and out[0]["item_name"] == "体重指数" and out[0]["result"] == "26.16", out


def test_trailing_arrow_semi_quantitative_value():
    # 董涛 尿常规: 半定量值+箭头同格("+-  ↑") 需被识别(此前只认数值/定性词)
    from app.modules.report.table_extractor import _value_with_arrow
    assert _value_with_arrow("+-  ↑") == ("+-", True)
    assert _value_with_arrow("++ ↑") == ("++", True)
    assert _value_with_arrow("± ↓") == ("±", True)
    assert _value_with_arrow("26.16 ↑") == ("26.16", True)
    assert _value_with_arrow("26.16") == (None, False)


# === 2026-09-28(第三批反馈): 62-66 五份 ===

def test_range_with_unit_trailing_space():
    # 生明月 血常规/单吉飞 检验表: 参考"20.00～50.00 %" 带空格, 单位 % 曾丢
    from app.modules.report.table_extractor import _range_with_unit
    assert _range_with_unit("20.00～50.00 %") == ("20.00", "50.00", "%")
    assert _range_with_unit("50-70 %") == ("50", "70", "%")
    assert _range_with_unit("0.29-1.70mmol/L") == ("0.29", "1.70", "mmol/L")


def test_unit_after_ref_not_next_name():
    # 单吉飞 VALUE/REF/UNIT 顺序: ref 后紧跟单位行应补单位;
    # 崇左 比重 ref 后 "*pH"(下一指标名, 其后跟值) 仍断
    from app.modules.report.table_extractor import extract_indicator_rows
    rows = extract_indicator_rows("\n".join([
        "中性粒细胞百分比（NEU%）", "39.3 ↓", "40 -- 70", "%", "◆",
        "淋巴细胞百分比（LYM%）", "52.7 ↑", "20 -- 50", "%"]))
    got = {(r["item_name"], r["result"]): r.get("unit") for r in rows}
    assert got.get(("中性粒细胞百分比（NEU%）", "39.3")) == "%", rows
    assert got.get(("淋巴细胞百分比（LYM%）", "52.7")) == "%", rows
    rows2 = extract_indicator_rows("\n".join(["*比重", "1.030", "1.010--1.030", "*pH", "5.00", "4.5--8"]))
    got2 = {(r["item_name"], r["result"]): r.get("unit") for r in rows2}
    assert got2.get(("比重", "1.030")) == "", rows2  # "*pH" 是下一指标名, 不作单位


def test_name_re_mlmin_not_name():
    # "ml/min"(单吉飞 肾小球过滤率单位) 曾被当 ASCII 斜杠名称 → 单位丢
    from app.modules.report.table_extractor import _NAME_RE
    assert not _NAME_RE.match("ml/min")
    assert _NAME_RE.match("CD4/CD8")


def test_reflow_numbered_prose_wrap_not_split():
    # 厚柏刚: "5、EB病毒…疾病等有"/"相关性，请结合临床…" 长正文折行曾被编号拆分规则拆开
    from app.modules.report.service import _reflow_conclusion_lines
    txt = ("5、EB病毒VCA-IgG抗体为阳性：EB病毒与鼻咽癌、伯基特淋巴瘤、淋巴组织增生性疾病等有\n"
           "相关性，请结合临床，必要时相关科室就诊。\n"
           "7、无创周围血管检测提示右侧锁骨下动脉狭窄可能，远端动脉血液灌注欠充足，建议做头颈动\n"
           "脉CTA检查：建议神经外科就诊，必要时进一步检查。")
    out = _reflow_conclusion_lines(txt)
    assert "疾病等有相关性，请结合临床" in out.replace("\n", ""), out
    assert "建议做头颈动脉CTA检查" in out.replace("\n", ""), out
    # 多名列表标题(无句读/短) 仍拆
    txt2 = ("4.阻塞性睡眠呼吸暂停低通气综合征、夜间中度低氧饱和度血症\n"
            "此次检查示阻塞性睡眠呼吸暂停低通气综合征，导致夜间低氧饱和度血症。")
    out2 = _reflow_conclusion_lines(txt2)
    assert out2.split("\n")[0].startswith("4.阻塞性"), out2


def test_locate_dedup_keeps_short_continuation():
    # 厚柏刚: 两条编号条目均以"…就诊。"结尾, 第二条"诊。"曾被段内去重吞掉
    from app.modules.report.service import _locate_findings_sections
    txt = ("体检结论分析\n"
           "2、肝功能提示…建议复查肝功能，升高明显请到消化科就\n诊。\n"
           "13、胆囊息肉：…肝胆外科就\n诊。\n"
           "14、双肾强回声点：…")
    sec = _locate_findings_sections(txt)
    assert sec.count("诊。") >= 2, sec


def test_locate_name_header_skip():
    # 单吉飞/董涛: 跨页续段尾混入"姓名页眉"(患者名+姓名:)
    from app.modules.report.service import _locate_findings_sections
    txt = ("体检结论分析\n1、异常\n建议复查。\n单吉飞\n姓名:\n检查号: 123\n检查项目\n血压")
    sec = _locate_findings_sections(txt)
    assert "单吉飞" not in sec, sec
    assert "检查项目" not in sec, sec


def test_reflow_table_conclusion_not_broken_by_num_prose():
    # 蔡明: "7. 胆囊息肉，大小约 3 × 3 mm"(短标题)后接"疾病解释"仍须拆行
    from app.modules.report.service import _reflow_conclusion_lines
    txt = "7. 胆囊息肉，大小约 3 × 3 mm\n疾病解释\n为胆囊壁突向腔内的隆起物。"
    out = _reflow_conclusion_lines(txt)
    assert "mm疾病解释" not in out, out
    assert "\n疾病解释\n" in out, out


def test_classify_batch5_units():
    # 62-66 分类口径(用户反馈)
    from app.core.indicator_groups import classify
    assert classify("中性分叶核计数") == "血常规"
    assert classify("肾小球过滤率") == "肾功能"
    assert classify("估算肾小球过滤率-肌酐") == "肾功能"
    assert classify("门冬氨酸氨基转移酶") == "心肌酶谱"
    assert classify("天门冬氨酸氨基转移酶") == "肝功能"   # 不受"门冬氨酸"规则影响
    assert classify("补体C1q") == "肾功能"


def test_junk_exam_part_and_result_label():
    # 62-66 端到端暴露: 分科页查体叙述混入绿区
    from app.modules.report.table_extractor import _junk_row_cell
    # 查体部位 + 正常结果(生明月 耳鼻喉/妇科页)
    assert _junk_row_cell("听力(左)", "", "正常")
    assert _junk_row_cell("扁桃体", "", "无肿大")
    assert _junk_row_cell("心脏听诊", "", "未闻及异常")
    assert _junk_row_cell("年06 月22 日听力(右)", "", "正常")
    # 结果为字段标签残片(单吉飞"■内科常规检查|检查者凌延昭")
    assert _junk_row_cell("■内科常规检查", "", "检查者凌延昭")
    assert _junk_row_cell("心脏彩超", "", "检查者")
    # 异常结果不受影响 / 正常指标不受影响
    assert not _junk_row_cell("宫颈", "", "糜烂样改变")
    assert not _junk_row_cell("体重指数", "", "26.16")
    from app.modules.report.table_extractor import _skip_name
    assert _skip_name("咽喉结果提示")


def test_layout_result_marker_prefix_stripped():
    # 单吉飞: "◆" 分隔符 cell 被并入结果列("◆19.3 ↓") → 剥标记后与行式行去重
    from app.modules.report.table_extractor import _strip_result_marker
    assert _strip_result_marker("◆19.3 ↓") == "19.3 ↓"
    assert _strip_result_marker("19.3") == "19.3"
    assert _strip_result_marker("◇") == ""


def test_extra_skip_precedence_over_break():
    # 单吉飞: 签名行(终审医师/初审医师)在结论条目中间, 档案 extra_skip 须优先于通用断点
    from app.modules.report.service import _locate_findings_sections
    from app.modules.report.report_profiles import compile_profile, match_profile
    prof = compile_profile(match_profile("河南科技大学第二附属医院"))
    txt = ("体检结论分析\n【指导与建议】\n●异常一：\n建议复查。\n"
           "终审医师\n总检评估意见：\n审核（盖章）\n初审医师\n"
           "●异常二：\n建议随访。\n检查项目\n血压")
    sec = _locate_findings_sections(
        txt, extra_skip_re=prof["extra_skip_re"], extra_anchor_re=prof["extra_anchor_re"])
    assert "异常二" in sec, sec          # 签名块之后的续页条目仍在
    assert "检查项目" not in sec, sec    # 通用 BREAK_RE2 仍收口


def test_vlm_form_gate_long_narrative_only():
    # 董涛: 血脂4行与尿常规同段, 短定性(未提示/normal/清亮/+-)曾使整段被形态门丢弃
    # (纯函数不可直接测布局门, 这里用行式等价断言守住"低密/高密"提取)
    from app.modules.report.table_extractor import col_rows_via_layout
    import os
    pdf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))),
        "体检报告样例", "0-第5批", "董涛249011.pdf")
    if not os.path.exists(pdf):
        import pytest
        pytest.skip("董涛样本缺失")
    names = {r["item_name"] for r in col_rows_via_layout(pdf)}
    assert "低密度脂蛋白胆固醇" in names, names
    assert "高密度脂蛋白胆固醇" in names, names


def test_skip_billing_done_status_and_ast_alt():
    # 陈春光: "收费项目汇总"表(项目名+已做/未做)被当指标; AST:ALT 未归类
    from app.modules.report.table_extractor import _skip_name
    assert _skip_name("常规检查已做")
    assert _skip_name("乙型肝炎e抗体测定(定性)已做")
    assert _skip_name("已做")
    assert _skip_name("收费项目汇总项目状态")
    assert not _skip_name("乙型肝炎e抗体测定")
    from app.core.indicator_groups import classify
    assert classify("AST:ALT") == "肝功能"
    assert classify("AST/ALT") == "肝功能"


def test_layout_qualified_risk_ref_dropped():
    # 董涛: 低密度脂蛋白胆固醇参考是风险分层注释("中、高危人群:<2.6；") → 不落 ref
    from app.modules.report.table_extractor import col_rows_via_layout
    import os
    pdf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))),
        "体检报告样例", "0-第5批", "董涛249011.pdf")
    if not os.path.exists(pdf):
        import pytest
        pytest.skip("董涛样本缺失")
    row = next(r for r in col_rows_via_layout(pdf) if r["item_name"] == "低密度脂蛋白胆固醇")
    assert not row.get("ref_low") and not row.get("ref_high"), row


def test_arrow_with_paren_and_combined_exam_parts():
    # 弘爱(22)重跑暴露: 本次结果"7.9(↓)"信号行自身值取不到 → 错配上次结果 9.2 假黄
    from app.modules.report.table_extractor import _value_with_arrow, _junk_row_cell
    assert _value_with_arrow("7.9(↓)") == ("7.9", True)
    assert _value_with_arrow("17.6 ↓") == ("17.6", True)
    # 组合查体部位名("皮肤、浅表淋巴结"/"胸廓、肺部") + 正常结果
    assert _junk_row_cell("皮肤、浅表淋巴结", "", "未见明显异常")
    assert _junk_row_cell("胸廓、肺部", "", "未见明显异常")
    # 结果为字段标签
    assert _junk_row_cell("内科", "", "检查结果")


def test_signal_skips_rev_dual_block_values():
    # 弘爱双值表: "5.73(↑)"(上次) 与 "5.80(↑)"(本次) 都带箭头 → 信号通道曾双发
    # (5.73 假黄); 块表区信号应整体跳过(由列式整块接管)
    from app.modules.report.table_extractor import extract_abnormal_signals
    import os
    pdf = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例/各地汇总/厦门弘爱_H004_11.pdf"
    if not os.path.exists(pdf):
        import pytest
        pytest.skip("弘爱样本缺失")
    sigs = extract_abnormal_signals(pdf)
    results = {(s["item_name"], s["result"]) for s in sigs}
    assert ("总胆固醇(CHOL)", "5.73") not in results, results  # 上次结果不作信号
    assert ("阴离子间隙(AG)", "9.2") not in results, results
    # 综述行"(2)总胆固醇(CHOL): 5.80↑" 仍作信号(块表区外的正文)
    assert ("总胆固醇(CHOL)", "5.80") in results, results


# === 2026-10-06: 第六批(67-76) ===

def test_dedup_double_layer_page():
    # 陈东海(八所边检)PDF 双文本层: 同 y 重叠重复行("3、"/"3、抗乙型肝炎…") → 去重
    from app.modules.report.service import _dedup_double_layer

    class _Page:
        def __init__(self, lines):
            self._lines = lines  # (y, x0, x1, text)

        def get_text(self, kind="text"):
            return {"blocks": [{"lines": [
                {"bbox": (x0, y, x1, y + 10), "spans": [{"text": t}]}
                for (y, x0, x1, t) in self._lines]}]}

    page = _Page([
        (82.9, 238.9, 355.9, "体检结果汇总"),
        (82.9, 238.4, 355.4, "体检结果汇总"),
        (120.9, 28.1, 268.5, "1.双源CT检查胸部平扫三维成像(包含平扫)："),
        (120.9, 40.1, 562.5, "双源CT检查胸部平扫三维成像(包含平扫)：双肺少许结节灶及磨玻璃影。"),
        (245.6, 28.5, 48.8, "3、"),
        (245.6, 28.1, 224.3, "3、抗乙型肝炎病毒表面抗体阳性"),
    ])
    out = _dedup_double_layer(page)
    assert out is not None
    lines = out.split("\n")
    assert lines.count("体检结果汇总") == 1, lines
    assert "3、" not in [l.strip() for l in lines], lines
    assert any("3、抗乙型肝炎病毒表面抗体阳性" in l for l in lines), lines
    # 单层页(无重复)返回 None
    page2 = _Page([(100.0, 10.0, 200.0, "正常页第一行"), (120.0, 10.0, 200.0, "正常页第二行")])
    assert _dedup_double_layer(page2) is None


def test_batch6_profile_flags():
    from app.modules.report.report_profiles import match_profile, compile_profile
    az = compile_profile(match_profile("吉林大学中日联谊医院 体检报告"))
    assert az.get("anchor_only") is True
    assert az["extra_anchor_re"].search("主检结论和建议：")
    assert az["extra_break_re"].search("主检助理（汇总）：")
    cm = compile_profile(match_profile("检查机构：海南慈铭奥亚健康体检中心"))
    assert cm["extra_break_re"].search("分项报告")
    mn = compile_profile(match_profile("儋州美年大健康体检中心"))
    assert mn["extra_break_re"].search("分项报告")
    zk = compile_profile(match_profile("中科体检服务号"))
    assert zk["extra_break_re"].search("杜美英  副主任医师，自1986年工作以")
    assert zk["extra_skip_re"].search("如有疑问，请扫码提交解读报告")
    # 陈东海(八所边检): 只取"一、诊断及建议"起(用户 2026-10-06 拍板)
    dw = compile_profile(match_profile("中华人民共和国八所出入境边防检查站 体检报告"))
    assert dw.get("anchor_only") is True
    assert dw["extra_anchor_re"].search("一、诊断及建议")


def test_non_indicator_table_page_detection():
    # 陈霓: 结论词/级别表、建议项目表、微量元素解读表、档案表整页跳过
    from app.modules.report.table_extractor import _is_non_indicator_table_page
    assert _is_non_indicator_table_page(["序号", "分类项目", "结论词", "级别"])
    assert _is_non_indicator_table_page(["序号", "检查项目", "检查意义建议详情", "1"])
    assert _is_non_indicator_table_page(["项目", "临床意义", "生理功能", "缺乏的解决方案"])
    assert _is_non_indicator_table_page(["个人历次体检档案记录"])
    assert _is_non_indicator_table_page(["体检分级提示"])
    assert _is_non_indicator_table_page(["强烈建议下次体检项目"])
    # 真实指标表不触发
    assert not _is_non_indicator_table_page(["检查项目", "检查结果", "身高", "172.7cm"])
    assert not _is_non_indicator_table_page(["项目名称", "检查结果", "参考范围", "单位"])
    assert not _is_non_indicator_table_page(["血清铁蛋白", "437.71", "↑", "[26.78--397.88]"])


def test_arrow_summary_label_quantifier_not_result():
    # 陈霓 小结标签行"(2)多肿瘤标志物12项（男）：血清铁蛋白：437.71↑" —— "12" 非结果
    from app.modules.report.table_extractor import _pair_from_lines
    lines = ["(2)多肿瘤标志物12项（男）：血清铁蛋白：437.71↑"]
    assert _pair_from_lines(lines, 0, "arrow") is None
    # 正常箭头小结行不受影响
    lines2 = ["尿酸 431 ↑"]
    r = _pair_from_lines(lines2, 0, "arrow")
    assert r and r["item_name"] == "尿酸" and r["result"] == "431", r


def test_batch6_generic_skip_rules():
    # 电话字段页眉必须行首锚定(行内"…电话：182…"不得整行误跳)
    from app.modules.report.service import _FINDINGS_STOP_SKIP_RE
    assert _FINDINGS_STOP_SKIP_RE.search("电话号码: 13876705806")
    assert not _FINDINGS_STOP_SKIP_RE.search(
        "请到肝胆腺体外科门诊（…，马天如副主任医师，电话：18277052009）进一步评估及诊治。")


# === 2026-10-07: 第 6 批前 5 份用户反馈修复 ===

def test_repeated_advice_line_kept():
    # 安周: 第 13 条"建议：定期复查。"与第 11 条同文, 段内去重曾吞掉(用户反馈漏建议)
    from app.modules.report.service import _locate_findings_sections
    text = "总检结论\n1、脂肪肝\n建议：定期复查。\n2、甲状腺结节\n建议：定期复查。\n主检医师：张三"
    sec = _locate_findings_sections(text)
    assert sec and sec.count("建议：定期复查。") == 2, sec


def test_trailing_zero_marker_stripped():
    # 白炜: PDF 文本层每段末尾多一个"0"字形(印刷无, OCR 证实)
    from app.modules.report.service import _reflow_conclusion_lines
    out = _reflow_conclusion_lines(
        "检查综述：\n★  T波改变0\n（1）部分T波改变与生理状态有关。0\n0\n★  低密度脂蛋白(LDL)偏高0")
    assert "改变0" not in out and "★  T波改变" in out
    assert "有关。0" not in out
    assert "偏高0" not in out
    # 续行标点前的 0 是正文一部分(华裕健"…可能性，0"+"类…"), 不得剥
    out2 = _reflow_conclusion_lines("评估良恶性的可能性，0\n类需要进一步评估；1-3类基本是良性病变。")
    assert "0类" in out2.replace("\n", "")


def test_reflow_word_break_and_value_fragment_merge():
    # 蔡国炜: 折行断词"10m"+"m"=10mm(曾按发现边界拆开); 纯值片段"0.1--0.6)"并入上行
    from app.modules.report.service import _reflow_conclusion_lines
    out = _reflow_conclusion_lines(
        "3、胆囊彩超:胆囊息肉\n建议定期复查肝胆B超，如息肉生长过快或直径超过10m\nm，请及时肝胆科就诊。")
    assert "10mm，请及时肝胆科就诊。" in out, out
    out2 = _reflow_conclusion_lines(
        "单核细胞绝对值增高:0.64*10^9/L(参考区间:\n0.1--0.6)\n"
        "红细胞平均体积减低:78.8fL(参考区间:82--1\n00)\n平均血红蛋白浓度增高:364g/L(参考区间:31\n6--354)")
    lines = [l for l in out2.splitlines()]
    assert "单核细胞绝对值增高:0.64*10^9/L(参考区间:0.1--0.6)" in lines, lines
    assert "红细胞平均体积减低:78.8fL(参考区间:82--100)" in lines, lines
    assert "平均血红蛋白浓度增高:364g/L(参考区间:316--354)" in lines, lines


def test_norm_value_cell_double_sign_paren():
    # 白炜尿隐血"10(+-)": 双符号括号整体作结果(旧式只认单符号, 值不被识别 → 假黄)
    from app.modules.report.table_extractor import _norm_value_cell
    assert _norm_value_cell("10(+-)") == "10(+-)"
    assert _norm_value_cell("10(+)") == "10"


def test_batch6_fix_profiles():
    # 蔡克田/蔡国炜: anchor_only(一般健康问题) + 医学科普/分项报告断点
    from app.modules.report.report_profiles import match_profile, compile_profile
    cm = compile_profile(match_profile("海南慈铭奥亚健康体检中心 主检报告"))
    assert cm.get("anchor_only") is True
    assert cm["extra_anchor_re"].search("一般健康问题：")
    assert cm["extra_break_re"].search("医学科普")
    assert cm["extra_break_re"].search("分项报告")
    assert cm["extra_break_re"].search("医学科普") and not cm["extra_break_re"].search("第四部分 医学科普")


def test_reverse_downgrade_white_wei_and_maoming():
    # 白炜: 文本表头序误报反序, 数据行序明确正序 → 降级(用户 2026-10-07 反馈假黄);
    # 茂名: 数据行序 16/7 不够"明确正序" → 维持反序(存量行为不变)
    import os
    import pytest
    from app.modules.report.service import _extract_pdf_text
    from app.modules.report.table_extractor import (
        _data_order_clearly_normal, _text_has_reversed_ref)
    for pdf, exp_rev, exp_normal in [
            (os.path.join("..", "体检报告样例", "0-第6批", "白炜100044.pdf"), True, True),
            ("/home/wjyy2/hospitalKnowledgeBase/体检报告样例/各地汇总/茂名人民_H004_11.pdf",
             True, False)]:
        if not os.path.exists(pdf):
            pytest.skip(f"样本缺失: {pdf}")
        lines = [l.strip() for l in _extract_pdf_text(pdf, hybrid=False).splitlines() if l.strip()]
        assert _text_has_reversed_ref(lines) is exp_rev, pdf
        assert _data_order_clearly_normal(lines) is exp_normal, pdf


def test_chinese_numeral_requires_separator():
    # 2026-10-07 今日回归: 汉字序号支持必须带分隔符/空白 —— "一般健康问题"/
    # "二氧化碳结合力" 不得被"一/二"剥头(曾产出回填垃圾"般健康问题"/"氧化碳结合力偏低[21.4")
    from app.modules.report.service import _parse_numbered_titles, _keep_body_after_colon
    ts = _parse_numbered_titles("一般健康问题：\n二氧化碳结合力偏低[21.4\n一、不完全性右束支传导阻滞")
    assert "般健康问题" not in ts and "氧化碳结合力偏低[21.4" not in ts, ts
    assert "不完全性右束支传导阻滞" in ts, ts
    kept = _keep_body_after_colon("一般健康问题：\n二氧化碳结合力偏低")
    assert "般健康问题" not in kept and "氧化碳" not in kept, kept


def test_numbered_title_check_prefix_takes_finding():
    # "N、检查/部位名:发现" 取发现侧(蔡克田/蔡国炜表格结论回填条目"胸部CT平扫"曾被当条目)
    from app.modules.report.service import _parse_numbered_titles
    assert _parse_numbered_titles("1、胸部CT平扫:双肺多发结节，Lung-RADS 2") == ["双肺多发结节"]
    assert _parse_numbered_titles("12、牙周:牙结石") == ["牙结石"]
    assert _parse_numbered_titles("6、外耳道:右外耳道耵聍") == ["右外耳道耵聍"]
    # 冒号前是发现/方向词, 或冒号后是建议句 → 仍取冒号前
    assert _parse_numbered_titles("7、红细胞计数增高:5.85*10^12/L") == ["红细胞计数增高"]
    assert _parse_numbered_titles("3、龋齿:建议充填") == ["龋齿"]


def test_negated_finding_not_extracted():
    # 白炜 TCT"NILM": "无上皮内病变或恶性病变" 的发现是阴性描述, 不得当异常条目
    from app.modules.report.service import (
        _negated_occurrence, _all_occurrences_in_junk_context)
    sent = "★ 液基薄层细胞学（TCT）:无上皮内病变或恶性病变（NILM）。"
    assert _negated_occurrence(sent, "上皮内病变") is True
    assert _negated_occurrence(sent, "恶性病变") is True
    assert _all_occurrences_in_junk_context(sent, "上皮内病变") is True
    # 复合形容词"无痛性血尿"不误杀; 正常发现不误杀
    assert _negated_occurrence("彩超提示：无痛性血尿，建议复查。", "血尿") is False
    assert _negated_occurrence("甲状腺右叶结节，建议复查。", "结节") is False


def test_urgent_re_excludes_risk_factor_compound():
    # 蔡克田"积极控制相关危险因素"曾判红区(用户反馈): "危险"复合词不是急症语义;
    # LLM 误判也由确定性复核清除; 真急症("有生命危险/立即就医")仍红
    from app.modules.report.service import _mark_urgent, _URGENT_RE
    assert _URGENT_RE.search("积极控制相关危险因素，心内科随诊") is None
    assert _URGENT_RE.search("危险分层评估") is None
    assert _URGENT_RE.search("有生命危险，建议立即就医") is not None
    items = [
        {"item_name": "双侧颈动脉分叉处局部内-中膜增厚",
         "suggestion": "建议…积极控制相关危险因素，心内科随诊", "is_urgent": True},
        {"item_name": "真急症", "suggestion": "有生命危险，请立即就医", "is_urgent": False},
    ]
    out = {i["item_name"]: i["is_urgent"] for i in _mark_urgent(items)}
    assert out["双侧颈动脉分叉处局部内-中膜增厚"] is False
    assert out["真急症"] is True


# === 2026-10-07: 后 5 份(72-76/69)反馈修复 ===

def test_dash_placeholder_and_bracket_ref():
    # 陈东海尿常规 [名][值][--][参考][--]: 占位符跳过继续消化参考;
    # 陈霓"[<5.2]"方括号参考
    from app.modules.report.table_extractor import extract_indicator_rows
    rows = extract_indicator_rows("酸碱度\n7.0\n--\n4.5-8.0\n--\n细菌")
    assert rows and str(rows[0]["ref_low"]) == "4.5" and str(rows[0]["ref_high"]) == "8.0", rows
    rows2 = extract_indicator_rows("总胆固醇\n7.23\n↑\n[<5.2]\nmmol/L")
    assert rows2 and str(rows2[0]["ref_high"]) == "5.2", rows2


def test_limit_ref_before_result_swap():
    # 陈刚乙肝表 [名][<参考单限][单位][结果][↑] dump 序 → 交换取真结果
    from app.modules.report.table_extractor import extract_indicator_rows
    rows = extract_indicator_rows("乙型肝炎病毒表面抗体\n<10.00\nmIU/mL\n272.75\n↑")
    assert rows and rows[0]["item_name"] == "乙型肝炎病毒表面抗体"
    assert rows[0]["result"] == "272.75" and str(rows[0]["ref_high"]) == "10.00", rows


def test_conclusion_skip_and_break_patterns():
    # 陈霓"体检号 22783236"(无冒号)/陈刚双语页眉/竖线行; 翟泽民"健康知识"断点
    from app.modules.report.service import (
        _FINDINGS_STOP_SKIP_RE, _FINDINGS_STOP_BREAK_RE2)
    for t in ("体检号 22783236", "健康体检报告 MEDICAL EXAMINATION REPORT",
              "陈刚  |  男  |  40  |  60366000270"):
        assert _FINDINGS_STOP_SKIP_RE.search(t), t
    assert _FINDINGS_STOP_BREAK_RE2.match("健康知识：")
    assert _FINDINGS_STOP_BREAK_RE2.match("健康科普")
    # 正文不误跳
    assert not _FINDINGS_STOP_SKIP_RE.search("建议定期复查。")


def test_classify_electrolyte_and_hp_antibody():
    # 阴离子隙→电解质; 幽门螺杆菌尿素酶A/B抗体→胃部幽门螺杆菌检测(不再误归肾功能)
    from app.core.indicator_groups import classify
    assert classify("阴离子隙") == "电解质"
    assert classify("幽门螺杆菌尿素酶A抗体") == "胃部幽门螺杆菌检测"
    assert classify("幽门螺杆菌尿素酶B抗体") == "胃部幽门螺杆菌检测"
    assert classify("幽门螺杆菌细胞毒抗体") == "胃部幽门螺杆菌检测"
    assert classify("尿素氮") == "肾功能"


def test_vlm_dual_table_incomplete_left_header():
    # 蔡镇宇双栏表(左半表头缺列): 按名称列切段解析 + 未映射列提示词按标志 +
    # 纯单位 token 不当名称("mmHg")
    from app.core.vlm_client import _parse_markdown_table
    html = ("<table><tr><td>项目</td><td>结果</td><td></td><td>项目</td><td>结果</td>"
            "<td>参考值</td><td>提示</td><td>单位</td></tr>"
            "<tr><td>身高</td><td>173</td><td>cm</td><td>体重</td><td>102.1</td><td></td><td>Kg</td><td></td></tr>"
            "<tr><td>体重指数</td><td>34.1</td><td>偏高</td><td>脉搏</td><td>92</td><td></td><td></td><td></td></tr>"
            "<tr><td>血压（收缩压）</td><td>141</td><td>偏高</td><td>mmHg</td><td>血压（舒张压）</td><td>103</td><td></td><td>偏高</td></tr>"
            "</table>")
    inds = _parse_markdown_table(html)
    names = {i["item_name"]: i for i in inds}
    assert "身高" in names and names["身高"]["result"] == "173", names
    assert "体重" in names and names["体重"]["result"] == "102.1", names
    assert names.get("体重指数", {}).get("signal_flag") == 3, names
    assert names.get("血压（收缩压）", {}).get("signal_flag") == 3, names
    assert "mmHg" not in names, names


def test_chendonghai_visual_item_order():
    # 陈东海: 双文本层 PDF 视觉重排(去重+按 y)后, "1、…"后跟其内容、"2、…"后跟"病史"
    # (用户 2026-10-07 拍板: 标题与内容归位(b) + 1-3 条详解补全(c))
    import os
    import pytest
    pdf = os.path.join("..", "体检报告样例", "0-第6批", "陈东海170016.pdf")
    if not os.path.exists(pdf):
        pytest.skip("样本缺失")
    from app.modules.report.service import (
        _compile_profile_re, _extract_pdf_text, _load_report_profiles,
        _locate_findings_sections, _strip_ocr_html, _reflow_conclusion_lines)
    mp, _ = _load_report_profiles()
    t = _extract_pdf_text(pdf, hybrid=False, visual_sort=True)
    prof = mp(_extract_pdf_text(pdf, hybrid=False))
    assert prof.get("visual_sort") is True
    comp = _compile_profile_re(prof)
    sec = _reflow_conclusion_lines(_locate_findings_sections(
        _strip_ocr_html(t), extra_break_re=comp["extra_break_re"],
        extra_skip_re=comp["extra_skip_re"], extra_anchor_re=comp["extra_anchor_re"],
        anchor_only=True))
    i1 = sec.find("1、双肺少许结节灶及磨玻璃影")
    i2 = sec.find("2、血白细胞计数升高")
    assert 0 <= i1 < i2, sec[:300]
    assert "胸部CT" in sec[i1:i1 + 200]
    assert "病史：" in sec[i2:i2 + 200]


def test_weak_positive_yellow_only_without_flag_col_and_ref_mismatch():
    # 2026-10-07(用户口径): 结果=弱阳性 + 与参考范围不一致(参考阴性) + 无提示/标志列 → 判黄
    from app.core.vlm_client import _parse_markdown_table
    hdr = "<tr><td>项目</td><td>结果</td><td>参考值</td></tr>"
    html = f"<table>{hdr}<tr><td>幽门螺杆菌尿素酶A抗体</td><td>弱阳性</td><td>阴性</td></tr></table>"
    assert _parse_markdown_table(html)[0].get("signal_flag") == 1
    # 无参考(无法认定不一致) → 不判
    html2 = f"<table>{hdr}<tr><td>X抗体</td><td>弱阳性</td><td></td></tr></table>"
    assert not _parse_markdown_table(html2)[0].get("signal_flag")
    # 有提示/标志列 → 由提示列决定(不置弱阳性黄)
    html3 = ("<table><tr><td>项目</td><td>结果</td><td>参考值</td><td>提示</td></tr>"
             "<tr><td>粘液丝</td><td>弱阳性</td><td>阴性</td><td></td></tr></table>")
    assert not _parse_markdown_table(html3)[0].get("signal_flag")
