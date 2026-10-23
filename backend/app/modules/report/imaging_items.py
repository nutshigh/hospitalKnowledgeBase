"""影像检查 11 项红黄绿总览(2026-10-14 v2 双源并集方案)。

数据源:
  源A: 最新 completed 解读的 indicator_judgment(source='conclusion')(item_name, color_level);
  源B: report_info.conclusion_text 原文收紧扫描(检查表头/正常句/化验行/长句截断 过滤)。
每项级别 = 两源取高(red > yellow > green)。纯函数, 不碰解读/提取管线。
spec: docs/superpowers/specs/2026-10-14-imaging-items-module-design.md
"""
import re

# === 11 项(顺序即前端展示顺序; sex: both/male/female) ===
BOTH, MALE, FEMALE = "both", "male", "female"
ITEMS = [
    ("心电图", BOTH),
    ("胸部X片", BOTH),
    ("胸部CT", BOTH),
    ("彩超(心脏)", BOTH),
    ("彩超(甲状腺)", BOTH),
    ("彩超(颈动脉)", BOTH),
    ("彩超(腹部+男泌尿系)", MALE),
    ("彩超(子宫附件)", FEMALE),
    ("彩超(乳腺)", FEMALE),
    ("彩超(腹部+女泌尿系)", FEMALE),
    ("头颅CT/核磁", BOTH),
]

# === 部位关键词(命中即标黄; 防误报口径见 spec: 不用裸"肺/脑/胆/内膜") ===
_ABDOMEN = ("肝|胆囊|胆管|胆壁|胆结石|胆息肉|胰腺|胰头|胰体|胰尾|脾|肾|膀胱|前列腺|"
            "输尿管|泌尿|脂肪肝|肝胆")
_PATTERNS = {
    "心电图": ("心电图|窦性|ST段|ST-T|T波|Q波|P波|P-R间期|PR间期|束支|房室传导|早搏|心律|Wellen|"
               "心电轴|电轴|高电压|低电压|QT间期|顺钟向|逆钟向|房颤|心动过缓|心动过速|房性|室性"),
    "胸部X片": "胸片|胸部正位|胸部X线|胸部X光",
    "胸部CT": ("胸部CT|双肺|左肺|右肺|两肺|肺部|肺叶|肺段|肺尖|肺野|肺门|肺内|"
               "肺纹理|肺气肿|肺大疱|肺大泡|肺纤维|肺结节|肺钙化|肺结核|肺粟粒|"
               "肺磨玻璃|磨玻璃|胸膜|纵隔|气管|支气管|粟粒|胸腺|"
               "肺(?!动脉|功能|通气|活量|弥散|静脉)"),
    "彩超(心脏)": ("心脏彩超|心脏超声|超声心动|二尖瓣|三尖瓣|主动脉瓣|肺动脉瓣|"
                   "左房|右房|左室|右室|左心房|右心房|左心室(?!高电压)|右心室(?!高电压)|"
                   "室间隔|房间隔|射血分数|LVEF|心包|瓣膜|左室舒张|主动脉窦部"),
    "彩超(甲状腺)": "甲状腺",
    "彩超(颈动脉)": "颈动脉|颈总动脉|颈内动脉|颈外动脉",
    "彩超(腹部+男泌尿系)": _ABDOMEN,
    "彩超(子宫附件)": "子宫|卵巢|附件|宫颈|盆腔|宫腔",
    "彩超(乳腺)": "乳腺|乳房|双乳|左乳|右乳|乳结节",
    "彩超(腹部+女泌尿系)": _ABDOMEN,
    "头颅CT/核磁": ("头颅|颅脑|颅内|颅骨|脑梗|脑缺血|脑萎缩|脑白质|脑出血|脑软化|"
                    "脑膜|脑动脉|腔隙性|垂体|小脑|脑室|脑干|丘脑|额叶|颞叶|"
                    "顶叶|枕叶|脑内"),
}
_KEYWORD_RE = {name: re.compile(p) for name, p in _PATTERNS.items()}

# === 过滤词表 ===
# 化验词(匹配前删除; 长词优先防截断残留)
# 2026-10-16: 科室名不是影像所见 —— "肾病科门诊就诊…"(尿常规建议)曾命中腹部+泌尿
# 彩超关键词"肾"→ 假黄(陈佳漩, 用户质问黄区来源) → 科室名整体屏蔽。
_DEPT_MASK = re.compile(
    "肾病科|肾内科|肾脏内科|泌尿外科|泌尿科|肝胆外科|肝胆科|心内科|心血管内科|"
    "消化内科|呼吸内科|内分泌科|甲状腺外科|乳腺外科|妇科|妇产科|眼科|耳鼻喉科|"
    "口腔科|普外科|神经内科|神经外科|血液科|肿瘤科|风湿免疫科|胸外科|骨科")
_LAB_MASK = re.compile("|".join(sorted([
    "宫颈防癌", "液基", "TCT",
    "肝功能", "肝纤维化检查", "肝功", "乙肝", "丙肝", "甲肝", "戊肝", "两对半", "肝炎病毒",
    "乙型肝炎", "丙型肝炎", "甲型肝炎", "戊型肝炎", "病毒性肝炎",
    "肾功能", "肾小球", "肾小管", "肾功", "肾病学科", "肾内科",
    "甲状腺功能", "甲功", "甲状腺抗体", "甲状腺球蛋白抗体", "过氧化物酶抗体",
    "甲状腺素", "促甲状腺",
    "肺功能", "肺通气", "肺活量", "肺弥散", "胰岛素",
    "血脂", "血糖", "血尿酸", "肌酐", "尿素氮", "载脂蛋白", "总胆固醇", "低密度脂蛋白",
    "高密度脂蛋白", "甲胎蛋白", "癌胚抗原", "糖类抗原", "FPSA", "TPSA", "PSA",
    "同型半胱氨酸", "铁蛋白", "神经元", "烯醇化酶", "胃泌素", "电解质", "血常规", "尿常规",
], key=len, reverse=True)))
# 正常句/未检句(子句级)
_NORMAL = re.compile(
    "未见异常|未见明显异常|未见明显|无明显异常|无异常|未见异常回声|未见占位|未见肿大|"
    "正常范围|大致正常|正常心电图|无殊|未见异常信号|均在正常|测值正常|正常大小|"
    "未见明显结构|未见明显病变|未见明确异常|功能正常|均正常|阴性|"
    "弃检|未检|放弃|显示不清|显示欠清|显示欠佳")
# 窦性心律(正常节律; 后接 不齐/过缓… 时保留)
_SOXIN = re.compile(r"窦性心律(?![不齐过缓速缓慢偏])")
# 化验强化标记行: 整行跳过(行长 >30)
_LAB_MARK = re.compile(
    "血脂|血糖|血尿酸|肌酐|尿素氮|载脂蛋白|甲胎蛋白|癌胚抗原|糖类抗原|FPSA|TPSA|"
    "同型半胱氨酸|铁蛋白|烯醇化酶|胃泌素|肌酸激酶|淀粉酶|脂肪酶|乳酸脱氢酶|转氨酶|"
    "胆红素|白蛋白|球蛋白|尿酸")
# 科普/建议标记: 长句在首个标记处截断(仅扫前缀); "监测动态心电图"/"检查提示"例外
_CUT = re.compile("|".join([
    "是指", "可见于", "常见于", "多见于", "发病率", "原因", "所致", "解释", "多数", "少数",
    "患者", "病人", "人群", "风险", "机制", "作用", "遗传", "症状", "治疗", "预防", "表现",
    "导致", "临床", "建议", "复查", "随访", "随诊", "就诊", "咨询", "注意", "饮食", "运动",
    "平时", "如果", "若", "一旦", "如需", "可致", "易患", "典型", "一般情况", "偶然发现",
    "无症状", "需要", "处理", "办法", "方法", "门诊", "检测", "测定", "试验", "证实", "表明",
    "密切相关", "包括", "医生", "生活质量", "危险因素", "增高", "降低", "升高", "疾病",
    "中耳", "鼻炎", "鼓膜", "定期", "避免", "保持", "加强", "控制", "减轻", "摄入", "食用",
    "服用", "检查(?![提示发现所见结果显示可见])", "监测(?!动态心电图)", "手术史", "必要时", "诊治", "见于",
    "耳穴", "穴位", "肺梗死", "肾脏受损", "脑膜炎",
    # 2026-10-19(韩先锋假黄): 因果科普句("可造成心、脑、肾等重要脏器损害"/
    # "轻微的上升可能与肝硬化、肝炎…")曾被部位词命中
    "造成", "引起", "上升", "下降",
]))
# 检查表头: 【…】内文以检查词结尾(可带结尾括号组; 003-26"螺旋CT平扫"类表头)
_EXAM_END = re.compile(
    r"(彩超|B超|超声|检查|心电图|CT|核磁|DR|正位|摄片|摄影|费|四项|功能|血流图|多普勒|"
    r"TCD|平扫|扫描|增强)"
    r"(?:[（(][^）)]*[）)]\s*)*$")
_BRACKET = re.compile(r"【([^】]{1,40})】")
# 行首页段标题(如"二、心电图室:")
_HEADLINE = re.compile(r"^[一二三四五六七八九十]+、.{0,15}(?:室|科|区)\s*[:：]?$")
# 标题行(以冒号结尾)的合法尾词: 仅这些是"纯检查名/引导语", 其后无发现内容
_TITLE_TAIL = re.compile(
    r"(?:检查|彩超|B超|超声|心电图|CT|核磁|DR|正位|摄片|摄影|平扫|扫描|增强|血流图|"
    r"多普勒|TCD|提示|室|科|区)$")
# 条目/段落边界(重置红关联块)
_BOUNDARY = re.compile(r"^(?:\d{1,3}[.、)）:：]|[一二三四五六七八九十百]+[、.）)]|[【★◆●○*▼►▶▍])")
# 保守红: 行动句 + 强紧急词; 条件句守卫(若/如…在行动词前)
_RED = re.compile(
    r"(?:建议|请|嘱)[^。；\n]{0,25}"
    r"(?:立即就医|立即就诊|尽快就诊|尽快就医|尽快手术|急诊|危急|切勿拖延|"
    r"及时就诊|及时就医|及时复诊|尽早|重视)")
_COND = re.compile(r"(如果|若|如|一旦)[^。；]{0,40}(建议|请|嘱)")

_LEVEL_RANK = {"green": 0, "yellow": 1, "red": 2}


def _collapse(s) -> str:
    return re.sub(r"\s+", "", s or "")


def _match_items(text: str) -> list:
    return [name for name, _ in ITEMS if _KEYWORD_RE[name].search(text)]


def _line_skip(line: str) -> bool:
    """检查表头 / 化验强化标记长行 / 经颅多普勒 → 不参与判黄。"""
    if _HEADLINE.match(line):
        return True
    # 2026-10-21: 妇科查体字段标签("宫颈:表面光滑"/"子宫位置:")不是影像所见
    if re.match(r"^(?:宫颈|子宫|附件|外阴|阴道)[^。；;]{0,10}[:：]", line):
        return True
    if re.search(r"经颅多普勒|颅内多普勒|TCD", line):
        return True
    core = re.sub(r"^[★◆●○*▼►▶▍①-⑩]+", "", line)
    if core.endswith((":", "：")):
        # 冒号标题: 仅在"尾词=纯检查名/提示"时跳过(如"心脏彩超:"/"…检查提示:");
        # 标题内含发现名时保留(如"*心电图提示J点上抬:")
        rest = core[:-1]
        if len(rest) <= 45 and _TITLE_TAIL.search(rest):
            return True
    if len(core) <= 45 and _EXAM_END.search(core):
        return True
    m = _BRACKET.search(line)
    if m and _EXAM_END.search(m.group(1).strip()):
        return True
    if _LAB_MARK.search(line) and len(line) > 30:
        return True
    return False


def _strip_list_prefix(c: str) -> str:
    c = re.sub(r"^[（(]\d{1,2}[）)]", "", c)
    return re.sub(r"^\d{1,3}[.、]", "", c)


def _scan_conclusion_text(text):
    """源B: 返回 (levels: {item: yellow|red}, evidence: {item: [命中行]})."""
    levels, evidence = {}, {}
    current = []
    _prev = ""
    for raw in (text or "").splitlines():
        line = _collapse(raw)
        if not line:
            continue
        # 2026-10-21: 化验解释的多行疾病枚举续行("皮肌炎、甲状腺功能减退…肺梗死等。")
        # 不是影像所见 —— 上一行含"多见于/可见于"且本行多顿号、以"等。"收尾 → 跳过。
        if (("多见于" in _prev or "可见于" in _prev or "常见于" in _prev)
                and line.count("、") >= 3 and line.endswith("等。")):
            _prev = line
            continue
        _prev = line
        if _BOUNDARY.match(line):
            current = []
        if not line.startswith(("建议", "请", "嘱")) and not _line_skip(line):
            if _BOUNDARY.match(line) or len(line) <= 60:
                clauses = [c.strip() for c in re.split("[。；;]", line) if c.strip()]
                if line.startswith("【"):
                    clauses = clauses[:1]      # 括号行只取首句(后续为科普解释)
                for c in clauses:
                    if _NORMAL.search(c):
                        continue
                    core = _strip_list_prefix(c)
                    if core.startswith(("如", "若", "一旦")):
                        continue
                    m = _CUT.search(core)
                    if m:
                        if m.start() == 0:
                            continue
                        core = core[:m.start()]
                    masked = _LAB_MASK.sub("", _DEPT_MASK.sub("", _SOXIN.sub("", core)))
                    for item in _match_items(masked):
                        levels.setdefault(item, "yellow")
                        if line not in evidence.setdefault(item, []):
                            evidence[item].append(line)
                        current.append(item)
        if current and _RED.search(line) and not _COND.search(line):
            for item in current:
                levels[item] = "red"
                if line not in evidence.setdefault(item, []):
                    evidence[item].append(line)
    return levels, evidence


def _entry_items(name) -> list:
    n = _collapse(str(name or ""))
    if not n or _NORMAL.search(n):
        return []
    return _match_items(_LAB_MASK.sub("", _DEPT_MASK.sub("", n)))


def build_imaging_items(conclusion_text, gender, entries=None) -> list:
    """双源并集 → 按性别过滤的 11 项 [{name, level}]。

    entries: [(item_name, color_level), ...] 最新 completed 解读的 source='conclusion' 行。
    conclusion_text 为空且无条目 → [] (前端显示"未提取到结论")。
    """
    if not _collapse(conclusion_text) and not entries:
        return []
    levels = {name: "green" for name, _ in ITEMS}
    for name, lvl in entries or []:
        lv = "red" if lvl == "red" else "yellow" if lvl == "yellow" else None
        if lv is None:
            continue
        for item in _entry_items(name):
            if _LEVEL_RANK[lv] > _LEVEL_RANK[levels[item]]:
                levels[item] = lv
    text_levels, _ = _scan_conclusion_text(conclusion_text)
    for item, lv in text_levels.items():
        if _LEVEL_RANK[lv] > _LEVEL_RANK[levels[item]]:
            levels[item] = lv
    g = _collapse(str(gender or ""))
    return [
        {"name": name, "level": levels[name]}
        for name, sex in ITEMS
        if not (sex == MALE and g == "女") and not (sex == FEMALE and g == "男")
    ]


def collect_evidence(conclusion_text, entries=None) -> dict:
    """审表用: 每项命中证据(先条目后原文, 带来源标签)。不参与级别计算。"""
    ev = {}
    for name, _lvl in entries or []:
        for item in _entry_items(name):
            ev.setdefault(item, []).append(f"[条目] {name}")
    _, text_ev = _scan_conclusion_text(conclusion_text)
    for item, lines in text_ev.items():
        for line in lines:
            ev.setdefault(item, []).append(f"[原文] {line}")
    return ev
