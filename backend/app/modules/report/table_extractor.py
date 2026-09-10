"""确定性指标行提取器(层1+层3, 2026-08-25)。

第一性原理: 指标行有硬结构信号 —— 名称 + 结果(数值或定性词)。
LLM 自由提取不稳定(梧州 67 vs 226 项), 改为规则定位:

- 行内模式: 一行内 "名称 数值 [单位] [范围]"  (常规表格/综述列表)
- 分行模式: 名称行 + 下一行纯值行("身高\\n156\\n体重\\n50.6", 北京模板两列表)
- 定性值: 正常/未见/无/未闻及/阴性/阳性 等与名称配对

层3校验: 名称+结果必须都存在; 按(名称,结果)去重。
"""
import re
from typing import List, Optional

_QUALITATIVE_WORDS = (
    "正常|未见异常|未见|未闻及|未触及|未检出|无异常|无|阴性|弱阳性|阳性|"
    "可疑|偏低|偏高|正常范围|轻度|中度|重度|检出|少量|\(\+\)|\(-\)|\(±\)|±|"
    "律齐|律不齐|无肿大|无压痛|无包块|无结节|无粘连|无分离|无移位|"
    "活动正常|搏动正常|未见肿大|未见明显异常|未见异常回声|无异常回声|"
    "清晰|对称|居中|光滑|呈正常生理弯曲|屈曲正常|未触及肿大|未扪及|"
    "第[0-9,，、\s]{1,15}项(阳性|阴性)|"
    "[-–—]|1\+|2\+|3\+|4\+|5\+|\d\+|±"
)
# === 2026-09-07: 值单元格规范化 ===
# ">1000[阳性反应（+）]" → ">1000"(符号+数字, 剥方括号); 半定量 "+1"/"1+2.0" 原样作值
_SIGNED_VAL_RE = re.compile(r"^([<>≤≥]?\s*[\d.]+)\s*(?:\[[^\[\]]*\])?$")
_SEMI_VAL_RE = re.compile(r"^[+]?[1-5]\+?[\d.]*$")
_COMBO_VAL_RE = re.compile(r"^([\d.]+)\s*[,，]\s*(超重|肥胖|偏高|偏低|升高|降低|阳性|异常)$")


def _norm_value_cell(ln: str) -> Optional[str]:
    """若整行是"可作结果的单元格"(符号数字+方括号注释/半定量), 返回规范化 result, 否则 None。"""
    m = _SIGNED_VAL_RE.match(ln)
    if m:
        return m.group(1).strip()
    if _SEMI_VAL_RE.match(ln):
        return ln.strip()
    m = _COMBO_VAL_RE.match(ln)
    if m:
        return m.group(1).strip()
    return None


# 值行: 数字[+空格][+单位(字母或汉字, 如"60 次/分")]; 不含负号(排除"140 -270"范围行)
# 2026-09-10: 汉字单位后缀必须带空格(如"60 次/分") —— 数字开头且与汉字无空格的
# 行是**指标名称**("25羟基维生素D" 池州/维生素D, 曾整行被当"值25+单位"漏提);
# 字母单位不带空格仍允许("42.85nmol/L")。
_VALUE_RE = re.compile(
    r"^([\d.]+)((?:\s+(?=[\u4e00-\u9fa5])[\u4e00-\u9fa5a-zA-Z%‰/·×^μ]+)|\s*[a-zA-Z%‰/·×^μ]*)$"
)
_QUAL_RE = re.compile(rf"^({_QUALITATIVE_WORDS})\s*[+\-]?$")
# 名称须含汉字(排除单位行"cm""ng/ml"); 允许前导标记(▲/▲★/★/＊/*, 滨州/北京/贵港表格,
# 可带空格);
# 2026-08-29: 允许希腊字母/字母/数字开头("γ-谷氨酰转移酶""*L-γ-谷氨酰基转移酶""C14尿素呼气试验")
_NAME_RE = re.compile(
    r"^[▲△★*＊]{0,2}\s?[A-Za-z0-9α-ωΑ-Ωγ#\-.()（）]*\s?[\u4e00-\u9fa5]"
    r"[\u4e00-\u9fa5A-Za-z0-9()（）%·#\-/α-ωΑ-Ωγ\[\]\. ]{0,24}$"
    r"|^(?i:(?!.*\/(?:mmol|umol|mg|ug|ng|pg|g|ml|dl|l|iu|u|fl)\s*$)"
    r"[A-Za-z][A-Za-z0-9]*(?:/[A-Za-z0-9]+)+$)"
    r"|^(?:pH|PH)$"
)
# 单位行: 字母/符号(可含数字如"10~9/L"); 2026-09-05: 允许 * 前缀(柳州 "*10^9/L")、全角 ％
_UNIT_RE = re.compile(
    r"^[*＊]?\s*[\d~a-zA-Z%％‰/·×^μ]+(?:\s+[a-zA-Z%％‰/·×^μ]+)?$"
)
# 范围行: "9-50" / "5.0-9.0" / "<5.2" / ">1.04" / "0-64" / "1.16--1.42" / "1.16–1.42" / "0.7～1.7"
# 2026-08-29: 单限 "～5.20"(防城港市中) "~5.17" 全/半角波浪号开头
# 2026-08-31: ↑/↓ 异常前缀(广西人民"↑208～428""↑<3.37", 可叠加符号)
# 2026-09-05: ≤/≥ 单侧上限/下限(德宏 BA% "≤1"、BA# "≤0.06")
_RANGE_RE = re.compile(
    r"^(?:[<>≤≥~～↑↓]\s*){0,2}[\d.]+\s*[-~～至–—]{1,2}\s*[\d.]+$"
    r"|^(?:[<>≤≥~～↑↓]\s*){0,2}[\d.]+$"
)
# 箭头行(偏高/偏低标记): "↑" "↓"
_ARROW_RE = re.compile(r"^[↑↓]$")
# 名称行黑名单(页眉/标题/结构化标签)
_SKIP_NAMES = re.compile(
    r"^(姓名|性别|年龄|体检日期|检查日期|审核日期|健康档案号|体检编号|登记号|电话|话$|建议$|弃检|未检|拒检|放弃检查|"
    r"项目名称|检查结果|检查医生|检查医师|审核医师|审核者|审核人|报告医师|科室小结|检查者|报告整理|"
    r"主检医师|总检医师|总检建议与结论|温馨提示|说明[:：]|"
    r"第\s*\d+\s*页|第|页/共|一般项目|内科查体|外科查体|一般检查|"
    r"参考范围|正常范围|参考区间|历史结果|提示参考范围|体检健康建议|健康建议|"
    r"总检建议|检查所见|"
    r"个/HP|个/视野|个/μl|个/μL|其它$|"
    r"报告日期|图像层厚|层距|电话|手机|终审日期|初审日期|打印日期|接收日期|总检时间|总审时间|总计|日期|"
    r"体检号[:：]?|体检者|体检号码|总检日期|查\s*体\s*号|查体日期|出生日期|档案ID|咨询电话|"
    r"服务热线|服务时间|报告编号|登记号|检查时间|检查医生|检查结论|体检条码|条码|男$|女$|每$|法$|缘$|白A|"
    r"参考值|次/分|"
    r"单位[:：]|单位$|部门[:：]|体检报告|健康体检报告|本体检报告|"
    r"体重超过标准体重|边缘|合适水平|升高|降低|提示|异常|现病史|既往史|家族史|"
    r"^(?:肾功|肝功|血脂|乙肝|丙肝|甲功|甲胎|心肌酶)\d*项?$|^比$|"
    r"大小约|小约为|直径约|"
    r"常规心电图|彩超|B超|超声|X线|DR|CT|MR|TCD|碳13|幽门螺杆菌(?!I?gG?|抗体|抗原|HP)|门诊|瞬时弹性成像)"
)
# 2026-09-05: 名称含词黑名单(单位/页眉嵌名:"福建省第二人民医院健康管理中心·体检报告")
_SKIP_SUB_RE = re.compile(
    r"(健康管理中心|健康体检中心|医院体检中心|·体检报告|健康管理有限公司|医院有限公司|"
    r"体检号|体检者|体检号码|历年对比|结果对比图|体检结果对比|对比图)"
)


# 名称续行(折行): 纯汉字短行, 非值/非名称/非跳过; 如"平均红细胞血红蛋白含\n量\n29.60"
_NAME_CONT_RE = re.compile(r"^[\u4e00-\u9fa5]{1,8}$")
# 2026-09-02: 纯括号折行("(定量)")—— 名称与值之间的折行修饰, 不单独成行
_PAREN_ONLY_RE = re.compile(r"^[（(][^（()）]*[)）]$")
# 2026-09-07: 拉丁缩写+括号续行("IgG(IgG/VCA)", 日照 EB 衣壳抗原名称续行) → 并入名称
_LATIN_PAREN_RE = re.compile(r"^[A-Za-z0-9/·.]+[（(][^（()）]*[)）]$")


def _skip_name(s: str) -> bool:
    """名称黑名单统一判定: 前缀黑名单 + 含词黑名单(页眉/单位名嵌名)。"""
    return bool(_SKIP_NAMES.match(s) or _SKIP_SUB_RE.search(s))


def _clean_name_tail(name: str) -> str:
    """2026-09-05: 名称尾词清洗 —— 综述行"名称偏高 (值"被名称组吞入的异常词/悬挂括号剥掉。
    注意须保留报告方把异常词当名称组成部分的情形较少, 此处只剥词缀组合形态。"""
    return re.sub(
        r"\s*(增高|升高|偏高|降低|偏低|异常|阳性|弱阳性|增快|过重|减少|超重|肥胖|偏轻|偏重)"
        r"\s*[（(]?$", "", name).strip()


def _is_value_line(s: str) -> bool:
    return bool(_VALUE_RE.match(s) or _QUAL_RE.match(s))


def _next_line_kind(lines: list[str], i: int) -> str:
    """下一行类型: value(结果/定性值) / unit / range / arrow / name / other"""
    if i + 1 >= len(lines):
        return "other"
    nxt = lines[i + 1]
    if _ARROW_RE.match(nxt):
        return "arrow"
    if _UNIT_RE.match(nxt):
        return "unit"
    if _RANGE_RE.match(nxt):
        return "range"
    if _is_value_line(nxt):
        return "value"
    if _NAME_RE.match(nxt) and not _skip_name(nxt):
        return "name"
    return "other"


def _merge_unclosed_paren_lines(lines: list[str]) -> list[str]:
    """2026-09-05: 括号折行合并 —— 行内 ( 或 （ 未闭合时拼下一行(仅拼一次)。
    福建第二"★乙型肝炎表面抗原测定（定\n性）" → 名称跨行断在括号内。
    不能循环拼(正文任一行"参考范围（"会把后续全文吞成一行)。
    """
    out: list[str] = []
    last_merged = False
    for ln in lines:
        if out and not last_merged and not ln and not out[-1]:
            continue
        if out and not last_merged and (out[-1].count("(") + out[-1].count("（")) > (out[-1].count(")") + out[-1].count("）")):
            out[-1] += ln
            last_merged = True
        else:
            out.append(ln)
            last_merged = False
    return out


def _merge_wrapped_names(lines: list[str]) -> list[str]:
    """预处理: 名称折行合并("平均红细胞血红蛋白含\\n量\\n29.60" → 名称拼一行)。"""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]

        if (
            i + 2 < n
            and not _is_value_line(ln)
            and _NAME_RE.match(ln) and not _skip_name(ln)
            and _NAME_CONT_RE.match(lines[i + 1])
            and not _is_value_line(lines[i + 1])  # 2026-09-05: 续行不得是定性值("正常/阴性"),
            # 否则"发育情况\n正常\n正常"式双列表格被误合并(崇左检体区)
            and lines[i + 1] not in ("男", "女", "年龄")  # 2026-09-07: 莆田头部"蔡芳坤/男/30岁"防误拼
            and _is_value_line(lines[i + 2])
        ):
            out.append(ln + lines[i + 1])
            i += 2  # 跳过名称行与续行, 下一轮处理值行
            continue
        out.append(ln)
        i += 1
    return out


def _merge_unclosed_square(lines: list[str]) -> list[str]:
    """2026-09-07: 方括号跨行(">1000[阳性反应\\n（+）]" 日照) → 拼到 ] 闭合。"""
    out: list[str] = []
    for ln in lines:
        if out and out[-1].count("[") > out[-1].count("]") and "[" in out[-1]:
            out[-1] += ln
        else:
            out.append(ln)
    return out


def _merge_para_wrap(lines: list[str]) -> list[str]:
    """2026-09-07: 综述段落长行折行导致的"半截名称行"修复 —— 厦门华西
    "(5)体检血脂：甘油三\\n酯：2.06mmol/L↑" 折行处把"酯"切到行首, 行内模式
    会把"酯：2.06"当指标(垃圾)。规则: 当前行 = "短名称(≤8汉字)+冒号+数值"且
    上一行以汉字结尾 → 上一行折行未完, 两行拼接后重扫。
    拼接后整行行首若为 (【 等(段落原行)则不会命中行内/名称分支, 一并消"臀围"折行垃圾。
    """
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if (
            i > 0
            and re.match(r"^[\u4e00-\u9fa5]{1,8}\s*[:：]\s*[\d.]+", ln)
            and re.search(r"[\u4e00-\u9fa5]$", out[-1])
            and len(out[-1]) < 150
        ):
            out[-1] += ln
            i += 1
            continue
        out.append(ln)
        i += 1
    return out


def _parse_ref(rest: str) -> tuple[Optional[str], Optional[str]]:
    """从行内剩余文本解析参考范围: "3.1-5.7" / "1.16--1.42" / "<5.2" / ">1.04" / "～5.20" / "(18-24)"。
    2026-08-31: 容忍 ↑/↓ 异常前缀(广西人民"↑208～428" —— ↑ 是报告方异常标记, 非 ref 一部分)。
    """
    m = re.search(r"\(?([<>≤≥↑↓]?[\d.]+\s*[-~～至–—]{1,2}\s*[\d.]+|[<>≤≥~～]\s*[\d.]+)\)?", rest)
    if not m:
        return None, None
    s = m.group(1)
    if s.startswith("<") or s.startswith("≤"):
        return None, s[1:].strip()
    if s.startswith(">") or s.startswith("≥"):
        return s[1:].strip(), None
    if s.startswith("~") or s.startswith("～"):
        return None, s[1:].strip()
    s = s.lstrip("↑↓")
    if "-" in s or "~" in s or "～" in s or "至" in s or "–" in s or "—" in s:
        lo, hi = re.split(r"[-~～至–—]{1,2}", s, maxsplit=1)
        return lo.strip(), hi.strip()
    return None, None


# === 2026-09-07: 参考值单元格带单位(厦门华西三列表 "0.29-1.70mmol/L" / "≤5ng/ml";
# 血常规 10^n 复合单位与范围数字粘连: "4.3-5.810^9/L" = hi 5.8 + 单位 10^9/L) ===
_REF_UNIT_SUFFIX_RE = re.compile(
    r"(10\^[0-9]+\s*/?\s*L?|mmol/L|umol/L|μmol/L|µmol/L|ng/ml|mg/L|g/L|IU/L|U/L|"
    r"mIU/mL|ml/min|IU/ml|ng/mL|μg/L|ug/L|pg/ml|mol/L|fL|fl|%)$"
)


def _range_with_unit(s: str) -> Optional[tuple]:
    """若 s 为"参考范围(可带单位)"单元格整行, 返回 (ref_low, ref_high, unit), 否则 None。
    2026-09-07: 剥单位后的 head 必须是纯范围形态 —— 注释文本("TPSA在4-20ug/L 时…")
    不能吞(FPSA/TPSA 行被注释 ref 污染判黄的回归教训)。
    """
    s = s.strip()
    unit = ""
    m = _REF_UNIT_SUFFIX_RE.search(s)
    if m:
        unit = m.group(1)
        s = s[:m.start()]
        # 10^n 复合单位与范围数字粘连: 单位 "10^9/L" 剥离后余尾 "10" 属单位前缀
        if unit.startswith("10^") and re.search(r"\d10$", s):
            s = s[:-2]
    if not s:
        return None
    if not re.fullmatch(
            r"[<>≤≥~～↑↓]?\s*[\d.]+\s*[-~～至–—]{1,2}\s*[\d.]+"
            r"|[<>≤≥~～↑↓]?\s*[\d.]+", s):
        return None
    lo, hi = _parse_ref(s)
    if lo or hi:
        return lo, hi, unit.strip()
    return None


# === 2026-09-06: "历年对比"表整段挖除 ===
# 德宏等报告含"历年对比"双年值列表(标题"历年对比" + 表头含 2025-xx-xx/2026-xx-xx 日期列,
# 值=历史/本年度, 无异常标志) —— 该表不是"检查指标明细表"(真表带 检查结果/H 提示列),
# 参考范围左值与历史值会被行式/列式误当指标(肌酐 57/3.4/0.4 绿区垃圾)。
# 规则: 独立标题行命中即挖到下一段落标题(本次体检结论/体检综述/总检建议等)前。
_HIST_CMP_TITLE_RE = re.compile(
    r"(历年对比|历年结果对比|历年体检结果|两年对比|近两年对比|"
    r"与既往我院体检结果对比图|历年体检结果对比图|体检结果对比图|结果对比图|"
    r"历次体检结果比对|历次体检对比)")
_HIST_CMP_END_RE = re.compile(
    r"^(本次体检结论|体检综述|体检健康建议|健康建议|总检建议|检查所见|科普说明|总检医生|"
    r"主检医师|报告医师|校对员|终审|操作员|--- Page |【)")


def _strip_history_compare_table(text: str) -> str:
    lines = (text or "").split("\n")
    out: list[str] = []
    skipping = False
    kept_skip = 0
    for ln in lines:
        s = ln.strip()
        if not skipping:
            if _HIST_CMP_TITLE_RE.search(s):
                skipping = True
                kept_skip = 0
                continue
            out.append(ln)
            continue
        kept_skip += 1
        if _HIST_CMP_END_RE.match(s):
            skipping = False
            out.append(ln)
            continue
        if kept_skip > 60:
            # 安全阀: 段尾标题缺失时不至于挖到 EOF(后续内容视为新段)
            skipping = False
            out.append(ln)
            continue
    return "\n".join(out)



# === 2026-09-07: 反列序表格检测 ===
# 茂名人民: 表头 dump = 项目名称/参考值/提示/检查结果/单位 —— "参考值"列在"检查结果"前,
# 视觉上每指标 ref 在名称/值之前 → 行式值行后 range 属下一指标, 不得向后消费。
# 检测: 表头窗口内 参考类词 index < 结果类词 index。
def _text_has_reversed_ref(lines: list[str]) -> bool:
    """反列序检测: 表头单元格**连续收集**(与列式表头收集同口径), 参考类词
    出现在结果类词**之前**(茂名: 项目名称/参考值/提示/检查结果/单位)。
    2026-09-07: 改为连续收集 —— 21 华西多表头密集时 ±8 窗口跨表会误判(清澈比重
    ref 被断、酸碱度 误配 1.003-1.030 的回归)。"""
    n = len(lines)
    for i in range(n):
        if not _COL_HEADER_START_RE.match(lines[i]):
            continue
        j = i + 1
        cells: list[str] = []
        while j < n and len(cells) < 6 and (
            _COL_HEADER_CELL_RE.match(lines[j]) or len(lines[j]) <= 2
        ):
            if _COL_HEADER_CELL_RE.match(lines[j]):
                cells.append(lines[j])
            j += 1
        if len(cells) < 2:
            continue
        ri = next((k for k, c in enumerate(cells) if c in ("参考值", "参考范围", "正常值")), None)
        si = next((k for k, c in enumerate(cells) if c in ("检查结果", "结果", "本次检查结果")), None)
        if ri is not None and si is not None and ri < si:
            return True
    return False


def extract_indicator_rows(text: str) -> list[dict]:
    """按行扫描, 提取(名称, 结果, 单位, 参考范围)对。支持行内/分行/定性值/序号制/名称折行。"""
    text = _strip_history_compare_table(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = _merge_unclosed_paren_lines(lines)
    lines = _merge_unclosed_square(lines)
    lines = _merge_para_wrap(lines)
    lines = _merge_wrapped_names(lines)
    # 2026-09-07: rev/dual 块表区由 extract_column_table_rows 整块接管, 行式不参与
    # (厦门弘爱双值表: 行式会把"参考列/说明行/上次值"当指标; 齐鲁反序表同理)
    if lines:
        _drop = sorted((a, b) for a, b, _m in _block_windows(lines))
        if _drop:
            kept: list[str] = []
            prev = 0
            for a, b in _drop:
                kept.extend(lines[prev:a])
                prev = b
            kept.extend(lines[prev:])
            lines = kept
    out: list[dict] = []
    _consumed_ref_lines: set[int] = set()  # 已被某值行后向消费的 range 行号(防前置 ref 误配)
    _rev_ref = _text_has_reversed_ref(lines)  # 2026-09-07: 参考列在结果列前的表(茂名)
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        # 2026-08-29: 桂林"名称结果高/低(值 单位)"行内标志(一行可多个,
        # 如"高密度脂蛋白结果低(1.47 mmol/L) 低密度脂蛋白结果高(2.77 mmol/L)")
        m3s = list(re.finditer(
            r"([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）·\-/]{0,20}?)结果(高|低)\(([\d.]+)",
            ln,
        ))
        if m3s:
            for m3 in m3s:
                if _skip_name(m3.group(1)):
                    continue
                out.append({
                    "item_name": m3.group(1).strip(),
                    "result": m3.group(3),
                    "unit": "",
                    "ref_low": None, "ref_high": None,
                    "signal_flag": 3,  # "结果高/结果低" = 报告方标志
                })
            i += 1
            continue
        # 行内模式: 名称 + 数字(可能带单位/范围)在同一行
        m = re.match(r"^([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·#\-/ ]{0,20}?)[\s:：]+([\d.]+)", ln)
        if m and not _skip_name(m.group(1)):
            rest = ln[m.end():]
            # 2026-09-05: 签名/日期字段("吴净瑛 2026-08-14 总检:…" "打印日期:2026-07-31")
            # 值 = 年份 + 余下 "-MM-DD" 日期形态 → 非指标, 跳过
            if re.match(r"^\s*-?\s*\d{1,2}\s*[-/]\s*\d{1,2}", rest) \
                    and re.match(r"^\d{2,4}$", m.group(2)):
                i += 1
                continue
            # 2026-09-07: 值后紧跟"、数字汉字"列举(健康科普"尿酸偏高:1、血尿酸偏高…")
            if re.match(r"^[、，,、]\s*[\u4e00-\u9fa5]", rest):
                i += 1
                continue
            name = m.group(1).strip()
            # 2026-09-05: 名称尾词清洗 —— "…偏高 (5.75" 等综述残行(未走全扫的形态)名称
            # 会被名称组吞入"偏高 (", 剥掉异常词尾缀与悬挂括号。
            name = re.sub(r"(增高|升高|偏高|降低|偏低|异常|阳性|增快|过重|减少|偏轻|超重|肥胖)\s*[（(]?$", "", name).strip()
            rest = ln[m.end():]
            # 单位清洗: 剥括号注释/箭头/多空格("IU/ml(0--34.00 IU/ml) ↑" → "IU/ml")
            unit = re.split(r"[（(]|[↑↓]|\s{2,}", rest)[0].strip()[:12]
            ref_lo, ref_hi = _parse_ref(rest)
            if name and not _skip_name(name):
                out.append({"item_name": name, "result": m.group(2), "unit": unit,
                            "ref_low": ref_lo, "ref_high": ref_hi})
            i += 1
            continue
        # 分行模式: 当前行是值行(含符号值">1000[..]"/半定量"+1" 形态)
        _norm_v = None if i == 0 else _norm_value_cell(ln)
        if (_is_value_line(ln) or _norm_v) and i > 0:
            prev = lines[i - 1]
            prev_is_name = (
                _NAME_RE.match(prev) and not _skip_name(prev)
                and not _is_value_line(prev) and not _PAREN_ONLY_RE.match(prev)
            )
            # 2026-09-02: 名称与值之间夹括号折行(广西人民"乙型肝炎表面抗体\n(定量)\n448.08"):
            # 括号行不是名称/值/单位/范围 → 原名配对失败、ref 丢失。跳过括号行再配对。
            # 2026-09-05: 括号含 ASCII 英文(如"(A-TPO)")= 名称的英文简称 → 并入名称;
            # 中文括号行("(定量)")维持跳板(避免 key 与列式/归一不一致)。
            name_offset = 1
            paren_part = ""
            if not prev_is_name and (_PAREN_ONLY_RE.match(prev) or _LATIN_PAREN_RE.match(prev)) and i >= 2:
                prev2 = lines[i - 2]
                if (_NAME_RE.match(prev2) and not _skip_name(prev2)
                        and not _is_value_line(prev2) and not _PAREN_ONLY_RE.match(prev2)):
                    prev = prev2
                    name_offset = 2
                    prev_is_name = True
                    if re.search(r"[A-Za-z]", lines[i - 1]):
                        paren_part = lines[i - 1]
            # 2026-09-07: 反列序表 提示列箭头夹在 名称 与 值 之间(茂名
            # "[ref]\n肌酐\n↑\n115.4\nμmol/L") → 值行前一行是箭头, 再前一行才是名称
            if not prev_is_name and _ARROW_RE.match(prev) and i >= 2:
                prev2 = lines[i - 2]
                if (_NAME_RE.match(prev2) and not _skip_name(prev2)
                        and not _is_value_line(prev2)):
                    prev = prev2
                    name_offset = 2
                    prev_is_name = True
                    row_flag = 2  # 箭头归属本指标(值行前箭头 = 报告方异常标志)
            if not prev_is_name:
                # 序号行(北京序号制表格): 上一行不是名称(是表头/范围/上一值)
                # 而当前是小整数序号 → 跳过
                i += 1
                continue
            # 结果行: 上一行是名称行 → 配对(两列表/三列表/序号制的值都在名称后)
            m2 = _VALUE_RE.match(ln)
            _norm_result = _norm_v if (m2 is None and _norm_v) else None
            unit = m2.group(2).strip() if m2 else ""
            # 2026-09-05: 值行后 单位/参考范围/箭头 顺序循环消化(马鞍山序号制含
            # "历史结果"单数列: "13.70|13.50|↓|15.5--18.1|fL" —— 单数行跳过,
            # 继续向后找真 ref/unit)
            ref_low = ref_high = None
            row_flag = 0
            # 2026-09-07: 反列序表(茂名 "[ref]\n名称\n[↑]\n值\n单位"): 名称前一行
            # RANGE = 本指标参考 → 优先于向后消化(值后 range 属下一指标, 曾错配假黄)
            prev_ref_idx = i - name_offset - 1
            prev_ref_avail = (
                _rev_ref
                and prev_ref_idx >= 0
                and prev_ref_idx not in _consumed_ref_lines
                and _RANGE_RE.match(lines[prev_ref_idx])
            )
            j = i + 1
            taken = 0
            while j < n and taken < 5:
                lj = lines[j]
                taken += 1
                if _is_block_name(lj) and not _is_value_line(lj):
                    break  # 2026-09-07: 值后消化遇下一指标名称即断(莆田 pH 行曾
                    # 被当单位/"6.0"被当历史列, 4.5-8.0 被误配给 维生素C)
                if _ARROW_RE.match(lj):
                    row_flag = max(row_flag, 2)
                    j += 1
                    continue
                if _FLAG_TEXT_RE.match(lj):
                    # 提示列文字("偏高"/"H" 等)夹在 值 与 单位/ref 之间(滨州/德宏) → 异常信号
                    row_flag = max(row_flag, 2)
                    j += 1
                    continue
                if _RANGE_RE.match(lj):
                    if (ref_low is None and ref_high is None) and _rev_ref:
                        break  # 反列序表: 参考列在名称前, 值后 range 属下一指标, 不向后消费
                    lo, hi = _parse_ref(lj)
                    if lo or hi:
                        if ref_low is not None or ref_high is not None:
                            break  # ref 已设 → 后续 range 属下一指标(崇左 比重后接 pH ref)
                        ref_low, ref_high = lo, hi
                        if lj.startswith(("↑", "↓")):
                            row_flag = max(row_flag, 2)  # "↑0～10.0": 参考行带箭头前缀 = 异常
                        _consumed_ref_lines.add(j)
                        j += 1
                        continue
                    j += 1  # 单数(历史结果列) → 跳过
                    continue
                # 2026-09-07: 参考范围带单位同行(厦门华西 "0.29-1.70mmol/L"/"≤5ng/ml",
                # "100-30010^9/L" 粘连) → 剥单位后解析 ref, 单位补空
                _ru = _range_with_unit(lj)
                if _ru and (ref_low is None and ref_high is None):
                    rlo, rhi, runit = _ru
                    ref_low, ref_high = rlo, rhi
                    _consumed_ref_lines.add(j)
                    if not unit and runit:
                        unit = runit
                    if lj.startswith(("↑", "↓")):
                        row_flag = max(row_flag, 2)
                    j += 1
                    continue
                if _UNIT_RE.match(lj):
                    if ref_low is not None or ref_high is not None:
                        break  # ref 已设 → 后续行是下一指标(崇左 比重 ref 后的 "*pH")
                    if not unit:
                        unit = lj.lstrip("*＊").strip()
                    j += 1
                    continue
                break
            # 2026-09-05: 参考范围列在名称**前**的排版(滨州/德宏反列序表格):
            # "59--104\n▲★尿酸\n478\n偏高" —— 名称前一行 RANGE 且未被任何值行消费 → 归属本行。
            prev_ref_idx = i - name_offset - 1
            if (ref_low is None and ref_high is None
                    and prev_ref_idx >= 0
                    and prev_ref_idx not in _consumed_ref_lines
                    and _RANGE_RE.match(lines[prev_ref_idx])):
                plo, phi = _parse_ref(lines[prev_ref_idx])
                if plo or phi:
                    ref_low, ref_high = plo, phi
                    _consumed_ref_lines.add(prev_ref_idx)

            result_v = m2.group(1) if m2 else (_norm_result or ln)
            _nm = _strip_refcell_prefix((prev + paren_part).lstrip("★*＊▲△"))
            row = {"item_name": _nm, "result": result_v, "unit": unit,
                   "ref_low": ref_low, "ref_high": ref_high}
            if row_flag:
                row["signal_flag"] = row_flag
            out.append(row)
            i += 1
            continue
        i += 1
    # 层3校验: 名称+结果齐全; 去重(名称,结果)
    seen: dict = {}
    deduped = []
    for row in out:
        key = (row["item_name"], row["result"])
        if key in seen:
            # 2026-09-07: 同 key 行取信息更全者 —— 综述行内命中(无 ref/flag)
            # 先出现会顶掉表格行(厦门华西"甘油三酯:2.06mmol/L↑…"综述先于表格)。
            old = deduped[seen[key]]
            old_has = (old.get("ref_low") or old.get("ref_high")) or old.get("signal_flag")
            new_has = (row.get("ref_low") or row.get("ref_high")) or row.get("signal_flag")
            if new_has and not old_has:
                deduped[seen[key]] = row
            elif new_has and old_has:
                if (row.get("ref_low") or row.get("ref_high")) and not (old.get("ref_low") or old.get("ref_high")):
                    deduped[seen[key]]["ref_low"] = row.get("ref_low")
                    deduped[seen[key]]["ref_high"] = row.get("ref_high")
                if row.get("signal_flag"):
                    deduped[seen[key]]["signal_flag"] = max(old.get("signal_flag", 0), row.get("signal_flag", 0))
            continue
        seen[key] = len(deduped)
        deduped.append(row)
    # 2026-08-31: 建议/科室文本残留("脂肪肝健康管理门诊" 建议行、
    # "瞬时弹性成像检查脂肪肝CAP值稍高(" 检查名)不是指标
    deduped = [
        r for r in deduped
        if not re.search(r"门诊|弹性成像|CAP值", r["item_name"])
    ]
    return deduped


def extract_indicators_from_pdf(pdf_path: str) -> list[dict]:
    import fitz
    doc = fitz.open(pdf_path)
    text = "".join(p.get_text() for p in doc)
    doc.close()
    return extract_indicator_rows(text)


# === 列式表格解析(2026-08-28): 广西模板"项目名称|检查结果|单位|参考范围|提示" ===
# fitz 文本提取把列式表格打散成逐行文本(名称/值/单位/ref/提示 每指标一个连续块)。
# 以表头序列为锚, 块内按 名称→值→单位→ref→提示 五元组聚合。
# 判定口径(2026-08-28 决策): 标志权威 —— 提示列/标志列的异常标志(偏高/↑/H...)
# 直接判黄(signal_flag=3, 不做 ref 数值复核); 弃检类不入库。
_COL_HEADER_START_RE = re.compile(
    r"^(项目名称|指标名称|检查项目|检验项目|检查内容|检查项目名称|测定项目|序号项目名称)$"
)
_COL_HEADER_CELL_RE = re.compile(
    r"^(检查结果|结果|测定值|单位|参考范围|参考值|正常值|提示|标志|临床意义|历史结果|提示参考范围)$"
)
_FLAG_TEXT_RE = re.compile(r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|A|\*)$")
_FLAG_ABNORMAL_RE = re.compile(
    r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|A|\*|增高↑|升高↑|降低↓|偏低↓)$"
)
_FLAG_NORMAL_RE = re.compile(r"^(正常|未见异常|无异常|阴性|弱阳性|阴性[（(]-?[)）]|阳性[（(]-?[)）]|±|-)$")
_FLAG_SKIP_RE = re.compile(r"^(弃检|未检|拒检|放弃|未查|无法完成|未测)$")
_SIGN_LINE_RE = re.compile(
    r"(检验师|检查医生|检查医师|审核医生|审核人|录入者|报告医师|主检|总检|签名|"
    r"日期[:：]\s*\d|第\s*\d+\s*页|共\s*\d+\s*页)"
)


def _assemble_column_row(name: str, block: list[str]) -> Optional[dict]:
    """五元组聚合: 名称 + [值|单位|ref|提示]。弃检不入库; 异常标志 → signal_flag=3。

    2026-08-29: 数字类行(含 > 前缀值 ">1000")统一按"首个=result, 其后=ref"分流。
    """
    row = {"item_name": name, "result": "", "unit": "", "ref_low": None, "ref_high": None,
           "signal_flag": 0}
    for b in block:
        if _FLAG_SKIP_RE.match(b):
            return None  # 弃检/未检: 不入库
        if _FLAG_ABNORMAL_RE.match(b):
            row["signal_flag"] = 3
            continue
        if _FLAG_NORMAL_RE.match(b):
            continue
        if _is_value_line(b) and not row["result"]:
            m = _VALUE_RE.match(b)
            if m and m.group(2):
                row["result"], row["unit"] = m.group(1), m.group(2).strip()
            elif m:
                row["result"] = m.group(1)
            else:
                row["result"] = b
            continue
        nvc = _norm_value_cell(b)
        if nvc and not row["result"]:
            row["result"] = nvc
            continue
        if re.match(r"^[<>≤≥~～]?\s*[\d.]+", b):
            # 数字类: 首个(含 > 前缀值如 "> 1000")为 result, 其后为 ref
            if not row["result"]:
                # 2026-08-31: 保留 >/< 符号(乙肝表面抗体 ">1000" 前端需显示符号);
                # ~～ ≤≥ 开头视为单限 ref 风格, 不当作 result 符号
                m = re.match(r"^([<>≤≥]?)\s*([\d.]+)", b)
                if not m:
                    continue
                row["result"] = (m.group(1) if m.group(1) in ("<", ">") else "") + m.group(2)
            else:
                lo, hi = _parse_ref(b)
                if lo or hi:
                    row["ref_low"], row["ref_high"] = lo, hi
            continue
        if _UNIT_RE.match(b) and not row["unit"]:
            row["unit"] = b
    if not row["result"]:
        return None
    return row


def extract_column_table_rows(text: str) -> list[dict]:
    """列式表格解析: 表头序列识别 → 块聚合。返回含 signal_flag 的行。

    2026-09-07: 支持两类"单元格 dump 顺序与视觉列序相悖"的新模板, 由表头词汇分派:
      - rev(齐鲁青岛): 视觉列 = 检查项目名称|检查结果|参考值|单位|异常标识,
        dump 序 = [异常标识][单位][参考值][检查结果][项目名](名在块尾)。
      - dual(厦门弘爱): 视觉列 = 检验项目|本次结果|上次结果|参考值,
        dump 序 = [项目名][参考*][上次值][本次值]; 异常标志 (↑/↓/*) 以括号挂在值后,
        "本次结果" = 块内最后一个值。
    两种模式产出行带 "__auth" 键, service 组装据此抑制行式/信号通道对表内名称的错配。
    """
    text = _strip_history_compare_table(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = _merge_unclosed_paren_lines(lines)
    lines = _merge_unclosed_square(lines)
    lines = _merge_para_wrap(lines)
    out: list[dict] = []
    i, n = 0, len(lines)
    # 2026-09-07: 反列序表(参考值列在检查结果列前, 茂名人民) —— 常规列式前向
    # 聚合会把"值后的下一指标 ref"当本指标 ref(col 覆盖行式正确结果);
    # 该形态由行式反列序逻辑(_rev_ref)承担, 常规 col 不再参与(rev/dual 块模式不受影响)。
    _col_rev_ref = _text_has_reversed_ref(lines)
    while i < n:
        if not (_COL_HEADER_START_RE.match(lines[i]) or lines[i] in ("本次结果", "上次结果")):
            i += 1
            continue
        # 表头词收集: START 命中行 ±6 行内找表头词汇(容忍 dump 乱序/夹杂)
        lo = max(0, i - 6)
        hi = min(n, i + 6)
        words: list[str] = []
        for t in range(lo, hi):
            x = lines[t]
            if (_COL_HEADER_START_RE.match(x) or _COL_HEADER_CELL_RE.match(x)
                    or x in ("本次结果", "上次结果", "异常标识")):
                words.append(x)
        mode = None
        if "异常标识" in words:
            mode = "rev"
        elif "本次结果" in words and "上次结果" in words:
            mode = "dual"
        if not mode and _col_rev_ref:
            # 反列序常规表: 跳过(行式反列序承担), 直到下一表头
            i += 1
            while i < n and not (_COL_HEADER_START_RE.match(lines[i])
                                 or lines[i] in ("本次结果", "上次结果")):
                i += 1
            continue
        if not mode:
            # 常规表头(原逻辑): 要求 START 行后跟 CELL 词
            j = i + 1
            header_cells: list[str] = []
            while j < n and len(header_cells) < 6 and (
                _COL_HEADER_CELL_RE.match(lines[j]) or len(lines[j]) <= 2
            ):
                if _COL_HEADER_CELL_RE.match(lines[j]):
                    header_cells.append(lines[j])
                j += 1
            if len(header_cells) < 2:
                i += 1
                continue
            k = j
            while k < n:
                ln = lines[k]
                if _COL_HEADER_START_RE.match(ln):
                    break
                if _SIGN_LINE_RE.search(ln):
                    k += 1
                    continue
                if _NAME_RE.match(ln) and not _skip_name(ln) and not _is_value_line(ln):
                    name = _strip_refcell_prefix(ln.lstrip("★*＊▲△").strip())
                    block: list[str] = []
                    m2 = k + 1
                    # 2026-09-05: 名称与值之间夹英文简称括号行("(A-TPO)")→ 并入名称
                    if m2 < n and _PAREN_ONLY_RE.match(lines[m2]) and re.search(r"[A-Za-z]", lines[m2]):
                        name += lines[m2].strip()
                        m2 += 1
                    while m2 < n and len(block) < 5:
                        b = lines[m2]
                        if _COL_HEADER_START_RE.match(b):
                            break
                        if re.match(r"^[*＊]\s*[A-Za-z]{1,8}$", b):
                            break  # 2026-09-05: 星号+纯字母 = 下一镜检/尿检项标记("*pH"), 防止串块
                        if (_NAME_RE.match(b) and not _skip_name(b) and not _is_value_line(b)
                                and not _FLAG_NORMAL_RE.match(b) and not _FLAG_ABNORMAL_RE.match(b)):
                            break  # 参考/提示形态(阴性(-)/A/H/L)不算名称行, 收进块
                        if _SIGN_LINE_RE.search(b):
                            m2 += 1
                            continue
                        if (_UNIT_RE.match(b) or _RANGE_RE.match(b) or _is_value_line(b)
                                or _range_with_unit(b) is not None
                                or _norm_value_cell(b) is not None
                                or _FLAG_ABNORMAL_RE.match(b) or _FLAG_NORMAL_RE.match(b)
                                or _FLAG_SKIP_RE.match(b)):
                            block.append(b)
                            m2 += 1
                        else:
                            break
                    row = _assemble_column_row(name, block)
                    if row:
                        out.append(row)
                    k = m2
                    continue
                k += 1
            i = k
            continue
        # rev / dual: 整窗口交给块模式解析, 窗口 = 最后一个表头词行之后到下一表头词/START
        word_idx = [t for t in range(lo, hi)
                    if (_COL_HEADER_START_RE.match(lines[t]) or _COL_HEADER_CELL_RE.match(lines[t])
                        or lines[t] in ("本次结果", "上次结果", "异常标识"))]
        body_start = max(word_idx) + 1
        body_end = n
        for t in range(body_start, n):
            x = lines[t]
            if _COL_HEADER_START_RE.match(x) or x in ("本次结果", "上次结果", "异常标识") \
                    or (_COL_HEADER_CELL_RE.match(x) and t > body_start):
                body_end = t
                break
        if mode == "rev":
            _parse_rev_window(lines, body_start, body_end, out)
        else:
            _parse_dual_window(lines, body_start, body_end, out)
        i = body_end
    # 去重
    seen: dict = {}
    deduped = []
    for row in out:
        key = (row["item_name"], row["result"])
        if key in seen:
            # 2026-09-07: 同 key 行取信息更全者 —— 综述行内命中(无 ref/flag)
            # 先出现会顶掉表格行(厦门华西"甘油三酯:2.06mmol/L↑…"综述先于表格)。
            old = deduped[seen[key]]
            old_has = (old.get("ref_low") or old.get("ref_high")) or old.get("signal_flag")
            new_has = (row.get("ref_low") or row.get("ref_high")) or row.get("signal_flag")
            if new_has and not old_has:
                deduped[seen[key]] = row
            elif new_has and old_has:
                if (row.get("ref_low") or row.get("ref_high")) and not (old.get("ref_low") or old.get("ref_high")):
                    deduped[seen[key]]["ref_low"] = row.get("ref_low")
                    deduped[seen[key]]["ref_high"] = row.get("ref_high")
                if row.get("signal_flag"):
                    deduped[seen[key]]["signal_flag"] = max(old.get("signal_flag", 0), row.get("signal_flag", 0))
            continue
        seen[key] = len(deduped)
        deduped.append(row)
    return deduped


# === 2026-09-07: rev(名称在后, 齐鲁青岛)与 dual(双值, 厦门弘爱)窗口解析 ===
# 值/定性单元格(不含参考范围与说明): 数字(可带尾括号异常标志)/半定量 d+/
# 免疫定性"阳性[234.27]"/纯定性词。参考行("65.00～85.00 g/L"/"成人:≤24 U/L"/
# "阴性-[<0.05] IU/mL")走 _RANGE_RE/_range_with_unit 或落到 ref_rows, 不算值。
_DUAL_QUAL_RE = re.compile(
    r"^(?:阴性|阳性|弱阳性|正常|未检出|淡黄色|黄色|清澈|浑浊|阴性或弱阳性|正常范围)"
)
_DUAL_VALUE_RE = re.compile(
    r"^(?:[<>≤≥]?\s*[\d.]+|[1-5]\+)(?:[（(](?:↑|↓|\*)[)）])?$"
    r"|^(?:阴性|阳性)\[[^\]】]+\](?:[（(](?:↑|↓|\*)[)）])?$"
    r"|^第\d+项(?:阳性|阴性|弱阳性)(?:[（(]\*[)）])?$"  # 厦门弘爱 "乙肝两对半结论: 第2项阳性(*)"
)
_BLOCK_FLAG_TAIL_RE = re.compile(r"[（(](?:↑|↓|\*)[)）]$")
_SIGN_TAIL_RE = re.compile(r"^(?:已检|未检|弃检|拒检)$")
# 块级名称判定放宽: 兼容名称内嵌 ASCII 斜杠缩写与冒号
# ("AST/ALT比值(AST/ALT)" / "载脂蛋白B:A1(APOB:A)") —— 仅块模式内使用
_BLOCK_NAME_FULL_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9()（）/:·.%#+\-]{2,40}"
)



# === 2026-09-07: dual/rev 块表窗口检测(供行式剔除, 避免行式在块表区产错位行) ===
def _block_windows(lines: list[str]):
    """扫描全部表头, 返回 [(body_start, body_end, mode)], mode in {"rev","dual"}。"""
    wins: list[tuple] = []
    n = len(lines)
    i = 0
    while i < n:
        if not (_COL_HEADER_START_RE.match(lines[i]) or lines[i] in ("本次结果", "上次结果")):
            i += 1
            continue
        lo, hi = max(0, i - 6), min(n, i + 6)
        words = [lines[t] for t in range(lo, hi)
                 if (_COL_HEADER_START_RE.match(lines[t]) or _COL_HEADER_CELL_RE.match(lines[t])
                     or lines[t] in ("本次结果", "上次结果", "异常标识"))]
        mode = "rev" if "异常标识" in words else (
            "dual" if "本次结果" in words and "上次结果" in words else None)
        if not mode:
            i += 1
            continue
        word_idx = [t for t in range(lo, hi)
                    if (_COL_HEADER_START_RE.match(lines[t]) or _COL_HEADER_CELL_RE.match(lines[t])
                        or lines[t] in ("本次结果", "上次结果", "异常标识"))]
        start = max(word_idx) + 1
        end = n
        for t in range(start, n):
            x = lines[t]
            if _COL_HEADER_START_RE.match(x) or x in ("本次结果", "上次结果", "异常标识") \
                    or (_COL_HEADER_CELL_RE.match(x) and t > start):
                end = t
                break
        wins.append((start, end, mode))
        i = end
    return wins



# === 2026-09-07: 参考/结果 cell 残留前缀剥离 ===
# 莆田 26: 上一 cell "阴性(-)"(HBSAG 参考)与下一名称粘连成一行 "阴性(-)乙肝表面抗体"
_REFCELL_PREFIX_RE = re.compile(r"^(?:阴性|阳性|弱阳性)\s*[（(]\s*-?[)）]\s*")


def _strip_refcell_prefix(name: str) -> str:
    m = _REFCELL_PREFIX_RE.match(name)
    if m:
        name = name[m.end():].strip()
    return name


def _is_block_name(b: str) -> bool:
    if _NAME_RE.match(b) and not _skip_name(b):
        return True
    return (not _skip_name(b)
            and _BLOCK_NAME_FULL_RE.fullmatch(b)
            and re.search(r"[\u4e00-\u9fa5]", b))


def _parse_rev_window(lines: list[str], start: int, end: int, out: list[dict]) -> None:
    """反序块: dump = [异常标识][单位?][参考值?][检查结果][项目名](名在块尾)。
    以名称为锚向上取 ≤4 行: 紧邻上 = result, 再上 range 行 = ref, 单位行 = unit,
    块首 ↑/↓ = 异常标志。"""
    i = start
    while i < end:
        ln = lines[i]
        if _COL_HEADER_START_RE.match(ln) or ln in ("本次结果", "上次结果", "异常标识") \
                or _SIGN_LINE_RE.search(ln):
            i += 1
            continue
        if _is_block_name(ln) and not _is_value_line(ln) \
                and not _DUAL_QUAL_RE.match(ln):
            name = _strip_refcell_prefix(ln.lstrip("★*＊▲△").strip())
            ups: list[str] = []
            t = i - 1
            while t >= start and len(ups) < 4:
                if lines[t].startswith("--- Page "):
                    break  # 2026-09-07: 页脚/页眉行不参与反序块装配
                if _is_block_name(lines[t]) \
                        or _COL_HEADER_START_RE.match(lines[t]):
                    break
                ups.append(lines[t])
                t -= 1
            if not ups:
                i += 1
                continue
            result = None
            flag = 0
            unit = ""
            ref_lo = ref_hi = None
            # 紧邻名称上方 = 检查结果单元格
            v0 = ups[0]
            if re.match(r"^[<>≤≥]?\s*[\d.]+$", v0):
                result = v0.strip()
            elif _is_value_line(v0) and not _SIGN_TAIL_RE.match(v0) and v0 != "-":
                result = v0
            # 2026-09-08: 跨页镜检表(齐鲁青岛 P14 大便区, 表头在 P13 尾) —— 每名前
            # 2-3 单元格 = [结果/标志(↑/-)][单位 /HP][参考(0~1/无)], 名在块尾;
            # 与主体 5 列(含结果列)不同。组内含 "/HP" 时按 镜检形态 装配:
            #   '/HP' 远侧行 = 结果/标志(↑ 判黄), '/HP' 近侧行 = 参考。
            if "/HP" in ups and len(ups) <= 4:
                # 视觉行 = 名称|结果|参考(空)|单位 /HP|标志(↑/-): dump = [标志][/HP][结果][名]
                # 2026-09-08(用户复核): 结果 = 紧邻名称上方行(脂肪滴 "0~1"), ↑ 仅为标志,
                # 无参考范围(齐鲁青岛 大便镜检区; 红细胞/白细胞结果为 "无" 或 "-")。
                for u in ups:
                    if re.match(r"^[↑↓]$", u):
                        flag = 3
                unit = "/HP"
                cand = ups[0]
                if cand != "/HP" and not re.match(r"^[↑↓]$", cand):
                    result = cand
            if result:
                for u in ups[1:]:
                    if _UNIT_RE.match(u) and not unit:
                        unit = u
                    elif _RANGE_RE.match(u) or _range_with_unit(u) is not None:
                        if ref_lo is None and ref_hi is None:
                            rlo, rhi, runit = _range_with_unit(u) or (*_parse_ref(u), "")
                            ref_lo, ref_hi = rlo, rhi
                            if runit and not unit:
                                unit = runit
                for u in ups:
                    if re.match(r"^[↑↓]$", u):
                        flag = 3
                        break
            row = {"item_name": name, "result": result or "-", "unit": unit,
                   "ref_low": ref_lo, "ref_high": ref_hi, "signal_flag": flag,
                   "__auth": True}
            out.append(row)
            i += 1
            continue
        i += 1


def _parse_dual_window(lines: list[str], start: int, end: int, out: list[dict]) -> None:
    """双值块: dump = [项目名][参考*][上次值][本次值]; 本次 = 块内最后一个值。
    参考行在值区前(可为多行叙述/带单位/单限/定性), 值带 (↑/↓/*) = 异常标志。"""
    i = start
    while i < end:
        ln = lines[i]
        if (_COL_HEADER_START_RE.match(ln) or ln in ("本次结果", "上次结果", "异常标识")):
            break
        if _SIGN_LINE_RE.search(ln):
            i += 1
            continue
        if _is_block_name(ln) and not _is_value_line(ln) \
                and not _DUAL_QUAL_RE.match(ln):
            name = ln.lstrip("★*＊▲△").strip()
            if i + 1 < end and _PAREN_ONLY_RE.match(lines[i + 1]) and re.search(r"[A-Za-z]", lines[i + 1]):
                name += lines[i + 1].strip()
                i += 1
            ref_rows: list[str] = []
            vals: list[str] = []
            t = i + 1
            while t < end:
                b = lines[t]
                if (_COL_HEADER_START_RE.match(b) or b in ("本次结果", "上次结果", "异常标识")):
                    break
                if b.startswith("--- Page "):
                    break  # 2026-09-07: 跨页页眉/页脚行不属表格块(弘爱双值表跨页续排)
                if _SIGN_LINE_RE.search(b):
                    t += 1
                    continue
                if _is_block_name(b) and not _is_value_line(b) \
                        and not _DUAL_QUAL_RE.match(b):
                    break  # 下一指标名
                if _DUAL_VALUE_RE.match(b) or _DUAL_QUAL_RE.fullmatch(b):
                    vals.append(b)
                    t += 1
                    continue
                ref_rows.append(b)  # 参考/说明行("成人:≤24 U/L"、"合适水平:<5.2…")
                t += 1
            if vals:
                cur = vals[-1]  # 本次结果(末值)
                if len(vals) >= 3:
                    ref_rows.append(vals[0])  # 参考列亦为定性形态(尿检 阴性/阴性/1+(*))
                row = {"item_name": name, "unit": "", "ref_low": None, "ref_high": None,
                       "signal_flag": 0, "__auth": True}
                flag = 0
                if _BLOCK_FLAG_TAIL_RE.search(cur):
                    flag = 3
                    cur = _BLOCK_FLAG_TAIL_RE.sub("", cur)
                row["result"] = cur
                row["signal_flag"] = flag
                for rl in ref_rows:
                    m = _range_with_unit(rl)
                    if m:
                        rlo, rhi, runit = m
                        if (row["ref_low"] is None and row["ref_high"] is None):
                            row["ref_low"], row["ref_high"] = rlo, rhi
                        if not row["unit"] and runit:
                            row["unit"] = runit
                        continue
                    if _UNIT_RE.match(rl) and not row["unit"]:
                        row["unit"] = rl
                    elif (row["ref_low"] is None and row["ref_high"] is None):
                        lo2, hi2 = _parse_ref(rl)
                        if lo2 or hi2:
                            row["ref_low"], row["ref_high"] = lo2, hi2
                out.append(row)
            i += 1
            continue
        i += 1


# === 个人信息规则提取(2026-08-25) ===
# 替代 LLM 提取 name/gender/age/report_date/unit_name; 失败字段返回 None, 由调用方回退 LLM。
# 2026-08-26: 柳州/梧州等"表头行+值行错位"PDF 修复:
#   - 姓名候选排除表头词(性别/部门名称/单位/体检号码...)
#   - 单位后紧跟 男/女 时, 该值为姓名(柳州"单位: 石坤 男 41岁")
#   - 姓名: 后 80 字窗口内第一个非表头词(梧州"姓名: 性别: 男 团体: ... 谢国宾")
_HEADER_WORD_RE = re.compile(
    r"^(性别|年龄|电话|单位|部门名称|员工编号|体检号码|打印日期|团\s*体|体检类型|"
    r"证件号码|健康档案|登记日期|首检日期|检查日期|体检日期|体检时间|姓名|性别)$"
)


def extract_personal_info(text: str) -> dict:
    import re as _re
    out: dict = {}
    m = _re.search(r"姓\s*名[:：]\s*([\u4e00-\u9fa5·]{2,6})", text)
    if m and not _HEADER_WORD_RE.match(m.group(1)):
        out["name"] = m.group(1).strip()
    # 兜底1: 单位后紧跟性别字 → 姓名(柳州"单位: 石坤 男 41岁")
    if not out.get("name"):
        m = _re.search(r"单\s*位[:：]\s*([\u4e00-\u9fa5·]{2,6})(?=\s*[（(]?(男|女))", text)
        if m and not _HEADER_WORD_RE.match(m.group(1)):
            out["name"] = m.group(1).strip()
    # 兜底2: 姓名: 后 80 字窗口内第一个非表头词(梧州"姓名: 性别: 男 团体: ... 谢国宾")
    if not out.get("name"):
        m = _re.search(r"姓\s*名[:：]", text)
        if m:
            window = text[m.end():m.end() + 80]
            for cand in _re.findall(r"[\u4e00-\u9fa5·]{2,6}", window):
                if not _HEADER_WORD_RE.match(cand):
                    out["name"] = cand
                    break
    m = _re.search(r"性\s*别[:：]\s*(男|女)", text)
    if m:
        out["gender"] = m.group(1)
    if not out.get("gender"):
        # 兜底: 头部区 男/女 后跟岁数(柳州"男 41岁" / 梧州表"谢国宾 | 男 | 36")
        m = _re.search(r"(男|女)[\s\S]{0,6}?\d{1,3}\s*岁", text[:1500])
        if m:
            out["gender"] = m.group(1)
    m = _re.search(r"年\s*龄[:：]\s*(\d{1,3})\s*岁?", text)
    if m:
        out["age"] = m.group(1)
    m = _re.search(r"(体检日期|检查日期|体检时间|登记日期|首检日期)\s*[:：]?\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if m:
        out["report_date"] = f"{m.group(2)}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    m = _re.search(r"单\s*位[:：]\s*([\u4e00-\u9fa5A-Za-z（）()]{2,30})", text)
    if m and not _re.search(r"(部门名称|单位名称|联系单位|检查单位)", m.group(1)):
        out["unit_name"] = m.group(1).strip()
    return out


# === 异常信号提取(2026-08-25): 黄区红区完全准确, 绿区可例外 ===
# 三个确定性信号通道, 命中即黄区候选(不需要解析全部表格式):
#   通道1 红字: span 颜色为红色系(#ff0000 近邻) → 北京/防城港一院等
#   通道2 箭头: 文本含 ↑/↓ → 广西多数 + 北京
#   通道3 异常词: 行文本含 增高/偏高/降低/偏低/异常/阳性/弱阳性 → 崇左类纯文本综述
# 输出 (item_name, result, unit); 绿区无信号行直接忽略。
_RED_SPAN_MAX = 120  # red 通道判定: r>200, g<max, b<max
_ABNORMAL_WORD_RE = re.compile(
    r"(增高|升高|偏高|降低|偏低|异常|阳性|弱阳性|可疑|超重|肥胖|偏轻|偏重|"
    r"增快|过重|↑|↓)"
)
# 异常词通道噪声: 标题/提示类行不做指标
_ABNORMAL_WORD_SKIP = re.compile(
    r"(体检结论|结论分析|测评总分|普检室|检查日期|体检报告|健康管理|温馨提示|"
    r"建议|请结合临床|单位[:：]|科室|医院体检中心|正常的|之间|参考范围)"
)


def _is_red_color(color: int) -> bool:
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return r > 200 and g < _RED_SPAN_MAX and b < _RED_SPAN_MAX


# 2026-09-05: 仅信号配对侧过滤的垃圾名(正常表格行仍保留 —— 体重/腰围等记录项)
_SIGNAL_ONLY_SKIP_RE = re.compile(r"^(体重|腰围|以下|本次体检结论|体检结论|结果|检查所见)$")


def _pair_from_lines(lines: list[str], idx: int, signal: str) -> Optional[dict]:
    """从信号行 idx 上下文中配对(名称, 结果): 信号行本身或其前后行。

    场景:
      - 行内: "尿酸 431 ↑" / "腰围 85 cm"(全红行)
      - 值行: "431\n↑"(信号行是值/箭头行, 向上找名称行, 向下找单位)
      - 通道3综述行: "【血脂四项】:(1)低密度脂蛋白增高4.73" → 名称=低密度脂蛋白 值=4.73
    """
    ln = lines[idx]
    # 2026-09-05: ▲/▲★ 标记行(滨州模板: "▲★尿酸 478 偏高")= 报告方异常标记
    if signal == "mark":
        # guard: ▲ 行本身或下方 4 行内须有异常佐证(偏高/升高/↓/H…), 否则该 ▲ 只是
        # 普通行标记(滨州全表行均带 ▲★, 不能全判异常)
        ctx = " ".join(lines[idx:min(idx + 4, len(lines))])
        if not re.search(r"(偏高|升高|增高|降低|偏低|异常|阳性|↑|↓|\bH\b)", ctx):
            return None
        ln = re.sub(r"^[▲△★*＊\s]+", "", ln)
        rest2 = re.sub(r"^(?:[（(]\d+[)）]|\d+\s*[、.．])\s*", "", ln)
        m = re.match(
            r"^([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·\-/]{0,20}?)[\s:：]*([\d.]+)",
            rest2,
        )
        if m and not _skip_name(m.group(1)):
            return {"item_name": _clean_name_tail(m.group(1)), "result": m.group(2),
                    "unit": "", "signal": signal}
        # 名称行形态("▲★白细胞数" 值在下一行) → 向下找值行
        for j in range(idx + 1, min(idx + 3, len(lines))):
            v = lines[j]
            m2 = _VALUE_RE.match(v)
            if m2:
                name = _clean_name_tail(re.sub(r"^[▲△★*＊\s]+", "", lines[idx]))
                return {"item_name": name, "result": m2.group(1),
                        "unit": m2.group(2).strip(), "signal": signal}
        return None
    # 通道3: 综述行内解析("【检查项】:(n)名称 异常词 数值")
    if signal == "word":
        rest = re.sub(r"^【[^】]*】[:：]?", "", ln)
        rest = re.sub(r"^[（(]\d+[)）]|^\d+[、.．]", "", rest).strip()
        m = re.match(
            r"^([\u4e00-\u9fa5A-Za-z()（）%·\-/ ]{1,20}?)"
            r"(增高|升高|偏高|降低|偏低|阳性|弱阳性|超重|肥胖|偏轻|偏重|增快|过重)[:：]?\s*([\d.]+)",
            rest,
        )
        if m and not _skip_name(m.group(1)):
            return {"item_name": _clean_name_tail(m.group(1)), "result": m.group(3), "unit": "", "signal": signal}
    # 信号行行内解析: 支持编号前缀 + 【】前缀 + 名称紧跟数字(崇左综述"(1)超重25.7 BMI")
    m = None
    for variant in (lines[idx], re.sub(r"【[^】]*】[:：]?", "", lines[idx], count=1)):
        m = re.match(
            r"^(?:[（(]\d+[)）、.。]?)?([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·\-/ ]{0,20}?)[\s:：]*([\d.]+)",
            variant,
        )
        if m:
            break
    if m and not _skip_name(m.group(1)):
        name = _clean_name_tail(m.group(1))
        if name and not _skip_name(name):
            return {"item_name": name, "result": m.group(2), "unit": "", "signal": signal}
    # 2026-09-07: 信号行本身是"名称行"(红名/行首名, 无数字) → 名=自身, 直接向下找值,
    # 不得向上找名(日照: 红名"天门冬氨酸氨基转移酶"向上 2 行撞到上一指标"谷丙/谷草"
    # 造成错配 0.92 假黄)。仅对 非名称形态 的信号行才向上找名。
    if signal == "red" and _NAME_RE.match(lines[idx]) and not _skip_name(lines[idx]) \
            and not _is_value_line(lines[idx]):
        name0 = _clean_name_tail(lines[idx].lstrip("★*＊▲△"))
        if name0 and not _skip_name(name0):
            for j in range(idx + 1, min(idx + 4, len(lines))):
                v = lines[j]
                if _ARROW_RE.match(v) or _FLAG_TEXT_RE.match(v) or _UNIT_RE.match(v) \
                        or _RANGE_RE.match(v):
                    continue
                m2 = _VALUE_RE.match(v)
                if m2:
                    return {"item_name": name0, "result": m2.group(1),
                            "unit": m2.group(2).strip(), "signal": signal}
                nv = _norm_value_cell(v)
                if nv:
                    return {"item_name": name0, "result": nv, "unit": "", "signal": signal}
                if _QUAL_RE.match(v):
                    return {"item_name": name0, "result": v, "unit": "", "signal": signal}
                if _NAME_RE.match(v):
                    break
            return None
    # 向上找名称行(≤3 行内, 跳过箭头/单位/范围/序号行; 中间夹英文括号行如
    # "(A-TPO)" 并入名称 —— 马鞍山表头/红字名称行与值间隔简称行)
    if _PAREN_ONLY_RE.match(ln) and re.search(r"[A-Za-z]", ln):
        _self_paren = ln  # 信号行自身是英文简称括号行(红字)
    else:
        _self_paren = ""
    for k in range(1, 4):
        if idx - k < 0:
            break
        cand = lines[idx - k]
        if _PAREN_ONLY_RE.match(cand):
            continue  # 纯括号行("(定量)/(A-TPO)")不是名称候选(并入由下方 bj 拼接处理)
        if _is_value_line(cand) or (_UNIT_RE.match(cand) and not _NAME_RE.match(cand)) \
                or _RANGE_RE.match(cand) or _ARROW_RE.match(cand):
            continue
        if _NAME_RE.match(cand) and not _skip_name(cand):
            name = cand.lstrip("★*＊▲△")
            # 名称与信号行之间夹的英文括号行并入名称(信号行自身为括号行时也并入)
            if _self_paren:
                name += _self_paren.strip()
            for bj in range(idx - k + 1, idx):
                if _PAREN_ONLY_RE.match(lines[bj]) and re.search(r"[A-Za-z]", lines[bj]):
                    name += lines[bj].strip()
            # 向下找值行
            for j in range(idx - k + 1, min(idx + 3, len(lines))):
                v = lines[j]
                m2 = _VALUE_RE.match(v)
                if m2:
                    return {"item_name": name, "result": m2.group(1), "unit": m2.group(2).strip(), "signal": signal}
                nv = _norm_value_cell(v)
                if nv:
                    return {"item_name": name, "result": nv, "unit": "", "signal": signal}
                if _QUAL_RE.match(v):
                    return {"item_name": name, "result": v, "unit": "", "signal": signal}
            return {"item_name": name, "result": "", "unit": "", "signal": signal}
    return None


def extract_abnormal_signals(pdf_path: str) -> list[dict]:
    """异常信号提取: 红字行 + 箭头行 + 异常词行 → (名称, 结果) 候选。

    信号行在**全文行**中定位, 以便向上配对黑色名称行/向下找值行。
    噪声过滤: 弃检/未检类提示(红字)剔除。
    """
    import fitz
    doc = fitz.open(pdf_path)
    all_lines: list[str] = []
    signal_idx: list[tuple[int, str]] = []
    for page in doc:
        d = page.get_text("dict")
        block_lines = []
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                t = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if t:
                    block_lines.append((t, line.get("spans", [])))
        # 2026-09-05: "历次体检结果比对/上次·本次体检结论"表(百色) = 结论对比文本,
        # 非检查项表格 → 标题行起(含)的该页内容不进信号通道。
        skip_from = next((i for i, (t, _) in enumerate(block_lines)
                          if "历次体检结果比对" in t or "历次体检对比" in t
                          or "历年对比" in t or "结果对比图" in t), len(block_lines))
        for line_idx, (text, spans) in enumerate(block_lines):
            if line_idx >= skip_from:
                continue
            if not text:
                continue
            if re.search(r"(弃检|未检|放弃|拒检|无法完成)", text):
                continue
            all_lines.append(text)
            idx = len(all_lines) - 1
            # 2026-09-07(口径确认): 红字样式不单独作为异常标志 —— 异常行均有
            # ↑↓/提示列字母(H/L/A)/提示文字/*等标志, 由下方分支/表格标志列承担。
            if "▲" in text or "△" in text:
                signal_idx.append((idx, "mark"))
            elif "↑" in text or "↓" in text:
                signal_idx.append((idx, "arrow"))
            elif _FLAG_TEXT_RE.match(text):
                # 2026-09-05: 表格提示列文字独立行("偏高" 值后, 滨州 FPSA/TPSA 行尾)
                signal_idx.append((idx, "flagtext"))
    doc.close()

    seen: dict = {}
    out = []
    for idx, sig in signal_idx:
        row = _pair_from_lines(all_lines, idx, sig)
        # result 为空 = 弃检/未检/配对失败, 不是异常信号, 剔除
        if row and row["item_name"] and row["result"]:
            # 2026-09-05: 配对值 = 阴性词("齿 正常"红字/整行标红的正常项)不是异常信号
            if re.fullmatch(r"(正常|未见异常|未见明显异常|无异常|未触及|未肿大|无肿大|未见|无|无明显异常)", row["result"]):
                continue
            if _SIGNAL_ONLY_SKIP_RE.match(row["item_name"]):
                continue
            # 2026-09-07: 检验科小结/建议句式残名("本次体检发现血甘油三酯:2.06↑")
            if row["item_name"].startswith("本次体检发现"):
                continue
            # 2026-09-07: 综述折行残名(厦门华西"甘油三\n酯:2.06mmol/L↑" → "酯")——
            # 信号通道按 pdf 行扫, 不经过 para 折行合并, 单/短汉字名一律拒收
            if len(re.findall(r"[\u4e00-\u9fa5]", row["item_name"])) <= 1 \
                    and len(row["item_name"]) <= 4:
                continue
            # 2026-09-07: 名称残留定性括号("(阴性(-)/(+)或…)" 莆田检验科小结) → 拒
            if re.search(r"[（(]\s*[+-]\s*[)）]", row["item_name"]) \
                    or re.match(r"^(阴性|阳性|弱阳性)[（(]-?[)）]?", row["item_name"]):
                continue
            key = (row["item_name"], row["result"])
            if key not in seen:
                seen[key] = True
                out.append(row)
    return out


# === Phase 2 接线(2026-09-07): 布局优先, 布局不可用(空/异常/图片型)回退旧列式 ===
def col_rows_with_fallback(pdf_path: str, text: str) -> list[dict]:
    try:
        rows = col_rows_via_layout(pdf_path)
        if rows:
            return rows
    except Exception:
        pass
    return extract_column_table_rows(text)


# === Phase 2(2026-09-07): 列语义组装(col_rows_via_layout) ===
# region(坐标检测) + 表头行列角色(x 区间) → 数据行按列归位取 名称/结果/参考/单位/标志。
# 统一覆盖: 常规列式表(广西)、华西三列表、弘爱双值表(表头"本次结果"直接给出结果列)、
# 齐鲁反序表(名称/值/参考/单位/异常标识 = 五列表头, 无需 rev 特判)。
# 值/参考的单元格判定复用本模块既有 helper(_norm_value_cell/_range_with_unit/_parse_ref)。
# ⚠ 未接线: 由 service 组装在验证通过后切换; 行式通道不变。
def col_rows_via_layout(pdf_path: str) -> list[dict]:
    """列语义组装(Phase 2 v6, 表头分段 + 段内独立列谱):
    - region 内每个表头行 → 新段; 段内数据行独立做 x0 列聚簇(容差 30);
    - 列数 > 角色数时剔除"窄标志列"(该列 ≥60% cell 为 ↑↓HL*/'-' 等短标记, 如华西 ↑ 列);
    - 等长 → 角色序 zip; 数据 cell 就近归列(容差 30), 归不进且近表头 x(≤60)也取角色;
    - region 首段包含表头前的页首块(华西上半血脂块)。
    ⚠ 未接线; 验证通过后由 service 组装切换。
    """
    from app.modules.report import layout as L

    regions = L.detect_table_regions(pdf_path)
    out: list[dict] = []
    _last_rx: Optional[list] = None
    for reg in regions:
        lines = L.logical_lines(reg.rows)
        # 切段: [(roles_xs, start_idx, end_idx)]; region 首段起点回拨到 0(页首块)
        segs: list = []
        cur_start = 0
        cur_roles = None
        for idx, cells in enumerate(lines):
            roles = L.header_roles(cells)
            if roles:
                if cur_roles is not None:
                    segs.append((cur_roles, cur_start, idx, False))
                cur_roles = [(r, c.x0) for r, c in zip(roles, cells)]
                cur_start = idx
        if cur_roles is not None:
            segs.append((cur_roles, cur_start, len(lines), False))
            _last_rx = cur_roles
        elif _last_rx is not None and lines:
            # 跨页/跨 region 续表(德宏毕建国化验大表): 继承最近表头谱(继承段)
            segs.append((_last_rx, 0, len(lines), True))
        for roles_xs, s0, s1 in segs:
            if s0 > 0 and segs[0][1] == 0:
                pass  # 首段含页首块(已在切段时 cur_start=0? 首表头前数据未被包)
        # 处理: 段 s0 的表头行之前若有数据(页首块), 归该段
        if segs:
            segs[0] = (segs[0][0], 0, segs[0][2])
        import os as _os
        _dbg = _os.getenv("LAYOUT_DEBUG")
        for roles_xs, s0, s1, inherited in segs:
            seg_rows: list[dict] = []
            seg_data = []
            for idx in range(s0, s1):
                cells = lines[idx]
                if L.header_roles(cells):
                    continue
                seg_data.append(cells)
            if _dbg and seg_data and any("肌酐" in c.text for cl in seg_data for c in cl):
                import sys as _s
                print("SEG roles:", [(r, round(x)) for r, x in roles_xs],
                      "cols:", [round(x) for x in col_xs], file=_s.stderr)
            cells_flat = [c for cl in seg_data for c in cl if c.text.strip()]
            if not cells_flat:
                continue
            xs = sorted(c.x0 for c in cells_flat)
            col_xs: List[float] = []
            for x in xs:
                if col_xs and x - col_xs[-1] <= 30.0:
                    col_xs[-1] = (col_xs[-1] + x) / 2
                else:
                    col_xs.append(x)
            pass
            # 窄标志列剔除(仅当 列数 > 角色数)
            while len(col_xs) > len(roles_xs):
                best_i = None
                best_ratio = 0.0
                for i, cx in enumerate(col_xs):
                    cs = [c.text.strip() for c in cells_flat
                          if abs(c.x0 - cx) <= 30.0]
                    if not cs:
                        continue
                    short = sum(1 for t in cs if len(t) <= 2
                                or t in ("↑", "↓", "H", "L", "*", "-", "异常"))
                    if short / len(cs) > best_ratio:
                        best_ratio = short / len(cs)
                        best_i = i
                if best_i is None or best_ratio < 0.6:
                    break
                col_xs.pop(best_i)
            for cells in seg_data:
                name_parts, result_parts, ref_parts, unit_parts, flag_parts = [], [], [], [], []
                float_flags: List[str] = []
                for c in sorted(cells, key=lambda c: c.x0):
                    col = min(range(len(col_xs)), key=lambda k: abs(c.x0 - col_xs[k]))
                    role = None
                    if col_xs and abs(c.x0 - col_xs[col]) <= 30.0:
                        if len(col_xs) == len(roles_xs):
                            role = roles_xs[col][0]
                    if role is None and roles_xs:
                        k = min(range(len(roles_xs)),
                                key=lambda i: abs(c.x0 - roles_xs[i][1]))
                        if abs(c.x0 - roles_xs[k][1]) <= 90.0:
                            role = roles_xs[k][0]
                            # 窄标志(↑↓HL)不应落非 flag 角色(体格表值后 ↑ 会被 ref 列吸走)
                            if c.text.strip() in ("↑", "↓", "H", "L") \
                                    and role != "flag":
                                role = None
                    t = c.text.strip()
                    if role is None:
                        if t in ("↑", "↓", "H", "L"):
                            float_flags.append(t)
                        continue
                    if role == "name":
                        name_parts.append(t)
                    elif role == "result":
                        result_parts.append(t)
                    elif role == "ref":
                        ref_parts.append(t)
                    elif role == "unit":
                        unit_parts.append(t)
                    elif role == "flag":
                        flag_parts.append(t)
                name = "".join(name_parts).strip()
                result_txt = "".join(result_parts).strip()
                if not name or not result_txt or _skip_name(name):
                    continue
                row = {"item_name": name.lstrip("★*＊▲△"), "result": result_txt,
                       "unit": "".join(unit_parts).strip(),
                       "ref_low": None, "ref_high": None, "signal_flag": 0,
                       "__auth": True}
                tail_flag = ""
                m = re.search(r"[（(](↑|↓|\*)[)）]$", result_txt)
                if m:
                    tail_flag = m.group(1)
                    result_txt = result_txt[:m.start()]
                nv = _norm_value_cell(result_txt)
                row["result"] = nv if nv is not None else result_txt.strip()
                for rt in reversed(ref_parts):
                    ru = _range_with_unit(rt)
                    if ru:
                        rlo, rhi, runit = ru
                        row["ref_low"], row["ref_high"] = rlo, rhi
                        if not row["unit"] and runit:
                            row["unit"] = runit
                        break
                else:
                    for rt in ref_parts:
                        lo, hi = _parse_ref(rt)
                        if lo or hi:
                            row["ref_low"], row["ref_high"] = lo, hi
                            break
                flag_txt = "".join(flag_parts)
                if (tail_flag or float_flags
                        or flag_txt in ("↑", "↓", "H", "L", "*", "异常")
                        or "*" in flag_txt
                        or re.search(r"(偏高|升高|增高|降低|偏低|阳性|异常)", flag_txt)):
                    row["signal_flag"] = 3
                out.append(row)
    # 结果形态门(报告级): result 应为 值/定性形态; 若大量 result 是单位/文本(表头-
    # 数据列倒挂, 如茂名人民陈灿明把 单位列当结果列 → result='μmol/L')→ 布局不可信,
    # 整体弃用, 交行式(反列序/序号制)兜底。
    if out:
        bad = 0
        for r in out:
            res = str(r["result"]).strip()
            if not (re.match(r"^[<>≤≥]?\s*[\d.]+", res)
                    or re.match(r"^(阴性|阳性|弱阳性|正常|未见|未检出|无|未及|1\+|2\+|[1-5]\+)", res)
                    or _norm_value_cell(res) is not None):
                bad += 1
        if bad / len(out) > 0.2:
            return []
    return out
