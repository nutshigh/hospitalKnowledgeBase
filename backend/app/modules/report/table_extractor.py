"""确定性指标行提取器(层1+层3, 2026-08-25)。

第一性原理: 指标行有硬结构信号 —— 名称 + 结果(数值或定性词)。
LLM 自由提取不稳定(梧州 67 vs 226 项), 改为规则定位:

- 行内模式: 一行内 "名称 数值 [单位] [范围]"  (常规表格/综述列表)
- 分行模式: 名称行 + 下一行纯值行("身高\\n156\\n体重\\n50.6", 北京模板两列表)
- 定性值: 正常/未见/无/未闻及/阴性/阳性 等与名称配对

层3校验: 名称+结果必须都存在; 按(名称,结果)去重。
"""
import re
from typing import Optional

_QUALITATIVE_WORDS = (
    "正常|未见异常|未见|未闻及|未触及|未检出|无异常|无|阴性|弱阳性|阳性|"
    "可疑|偏低|偏高|正常范围|轻度|中度|重度|检出|少量|\(\+\)|\(-\)|\(±\)|±|"
    "律齐|律不齐|无肿大|无压痛|无包块|无结节|无粘连|无分离|无移位|"
    "活动正常|搏动正常|未见肿大|未见明显异常|未见异常回声|无异常回声|"
    "清晰|对称|居中|光滑|呈正常生理弯曲|屈曲正常|未触及肿大|未扪及|"
    "[-–—]|1\+|2\+|3\+|4\+|5\+|\d\+|±"
)
# 值行: 数字[+空格][+单位(字母或汉字, 如"60 次/分")]; 不含负号(排除"140 -270"范围行)
_VALUE_RE = re.compile(r"^([\d.]+)\s*([\u4e00-\u9fa5a-zA-Z%‰/·×^μ]*)$")
_QUAL_RE = re.compile(rf"^({_QUALITATIVE_WORDS})$")
# 名称须含汉字(排除单位行"cm""ng/ml"); 允许前导标记(★/＊/*, 北京/贵港表格, 可带空格);
# 2026-08-29: 允许希腊字母/字母/数字开头("γ-谷氨酰转移酶""*L-γ-谷氨酰基转移酶""C14尿素呼气试验")
_NAME_RE = re.compile(
    r"^[★*＊]?\s?[A-Za-z0-9α-ωΑ-Ωγ\-.()（）]*[\u4e00-\u9fa5]"
    r"[\u4e00-\u9fa5A-Za-z0-9()（）%·\-/α-ωΑ-Ωγ\[\]\.]{0,24}$"
)
# 单位行: 字母/符号(可含数字如"10~9/L")
_UNIT_RE = re.compile(r"^[\d~a-zA-Z%‰/·×^μ]+$")
# 范围行: "9-50" / "5.0-9.0" / "<5.2" / ">1.04" / "0-64" / "1.16--1.42" / "1.16–1.42" / "0.7～1.7"
# 2026-08-29: 单限 "～5.20"(防城港市中) "~5.17" 全/半角波浪号开头
# 2026-08-31: ↑/↓ 异常前缀(广西人民"↑208～428""↑<3.37", 可叠加符号)
_RANGE_RE = re.compile(
    r"^(?:[<>~～↑↓]\s*){0,2}[\d.]+\s*[-~～至–—]{1,2}\s*[\d.]+$"
    r"|^(?:[<>~～↑↓]\s*){0,2}[\d.]+$"
)
# 箭头行(偏高/偏低标记): "↑" "↓"
_ARROW_RE = re.compile(r"^[↑↓]$")
# 名称行黑名单(页眉/标题/结构化标签)
_SKIP_NAMES = re.compile(
    r"^(姓名|性别|年龄|体检日期|检查日期|健康档案号|体检编号|登记号|电话|话$|"
    r"项目名称|检查结果|科室小结|检查者|报告整理|主检医师|总检医师|总检建议与结论|温馨提示|说明[:：]|"
    r"第\s*\d+\s*页|一般项目|内科查体|外科查体|一般检查|"
    r"参考范围|正常范围|参考区间|"
    r"个/HP|个/视野|个/μl|个/μL|其它$|"
    r"报告日期|图像层厚|层距|电话|"
    r"单位[:：]|部门[:：]|体检报告|健康体检报告|本体检报告|"
    r"常规心电图|彩超|B超|超声|X线|DR|CT|MR|TCD|碳13|幽门螺杆菌|门诊|瞬时弹性成像)"
)


# 名称续行(折行): 纯汉字短行, 非值/非名称/非跳过; 如"平均红细胞血红蛋白含\n量\n29.60"
_NAME_CONT_RE = re.compile(r"^[\u4e00-\u9fa5]{1,8}$")
# 2026-09-02: 纯括号折行("(定量)")—— 名称与值之间的折行修饰, 不单独成行
_PAREN_ONLY_RE = re.compile(r"^[（(][^（()）]*[)）]$")


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
    if _NAME_RE.match(nxt) and not _SKIP_NAMES.match(nxt):
        return "name"
    return "other"


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
            and _NAME_RE.match(ln) and not _SKIP_NAMES.match(ln)
            and _NAME_CONT_RE.match(lines[i + 1])
            and _is_value_line(lines[i + 2])
        ):
            out.append(ln + lines[i + 1])
            i += 2  # 跳过名称行与续行, 下一轮处理值行
            continue
        out.append(ln)
        i += 1
    return out


def _parse_ref(rest: str) -> tuple[Optional[str], Optional[str]]:
    """从行内剩余文本解析参考范围: "3.1-5.7" / "1.16--1.42" / "<5.2" / ">1.04" / "～5.20" / "(18-24)"。
    2026-08-31: 容忍 ↑/↓ 异常前缀(广西人民"↑208～428" —— ↑ 是报告方异常标记, 非 ref 一部分)。
    """
    m = re.search(r"\(?([<>↑↓]?[\d.]+\s*[-~～至–—]{1,2}\s*[\d.]+|[<>~～]\s*[\d.]+)\)?", rest)
    if not m:
        return None, None
    s = m.group(1)
    if s.startswith("<"):
        return None, s[1:]
    if s.startswith(">"):
        return s[1:], None
    if s.startswith("~") or s.startswith("～"):
        return None, s[1:].strip()
    s = s.lstrip("↑↓")
    if "-" in s or "~" in s or "～" in s or "至" in s or "–" in s or "—" in s:
        lo, hi = re.split(r"[-~～至–—]{1,2}", s, maxsplit=1)
        return lo.strip(), hi.strip()
    return None, None


def extract_indicator_rows(text: str) -> list[dict]:
    """按行扫描, 提取(名称, 结果, 单位, 参考范围)对。支持行内/分行/定性值/序号制/名称折行。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = _merge_wrapped_names(lines)
    out: list[dict] = []
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
                if _SKIP_NAMES.match(m3.group(1)):
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
        m = re.match(r"^([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·\-/ ]{0,20}?)[\s:：]+([\d.]+)", ln)
        if m and not _SKIP_NAMES.match(m.group(1)):
            name = m.group(1).strip()
            rest = ln[m.end():]
            ref_lo, ref_hi = _parse_ref(rest)
            out.append({"item_name": name, "result": m.group(2), "unit": rest.strip()[:12],
                        "ref_low": ref_lo, "ref_high": ref_hi})
            i += 1
            continue
        # 分行模式: 当前行是值行
        if _is_value_line(ln) and i > 0:
            prev = lines[i - 1]
            prev_is_name = (
                _NAME_RE.match(prev) and not _SKIP_NAMES.match(prev)
                and not _is_value_line(prev) and not _PAREN_ONLY_RE.match(prev)
            )
            # 2026-09-02: 名称与值之间夹括号折行(广西人民"乙型肝炎表面抗体\n(定量)\n448.08"):
            # 括号行不是名称/值/单位/范围 → 原名配对失败、ref 丢失。跳过括号行再配对。
            name_offset = 1
            if not prev_is_name and _PAREN_ONLY_RE.match(prev) and i >= 2:
                prev2 = lines[i - 2]
                if (_NAME_RE.match(prev2) and not _SKIP_NAMES.match(prev2)
                        and not _is_value_line(prev2) and not _PAREN_ONLY_RE.match(prev2)):
                    prev = prev2
                    name_offset = 2
                    prev_is_name = True
            if not prev_is_name:
                # 序号行(北京序号制表格): 上一行不是名称(是表头/范围/上一值)
                # 而当前是小整数序号 → 跳过
                i += 1
                continue
            # 结果行: 上一行是名称行 → 配对(两列表/三列表/序号制的值都在名称后)
            m2 = _VALUE_RE.match(ln)
            unit = m2.group(2).strip() if m2 else ""
            # 2026-09-02: 值行后 单位/参考范围 顺序兼容两种列序:
            #   广西 "21\n15～40\nU/L" = 值→ref→单位
            #   北京序号制 "14.6\nU/L\n13-35" 与 "16.9\n↑\numol/L\n5.9-16" = 值→(↑)→单位→ref
            ref_low = ref_high = None
            j = i + 1
            if j < n and _ARROW_RE.match(lines[j]):
                j += 1
            if j < n and _RANGE_RE.match(lines[j]):
                ref_low, ref_high = _parse_ref(lines[j])
                j2 = j + 1
                if not unit and j2 < n and _UNIT_RE.match(lines[j2]):
                    unit = lines[j2].strip()
            elif j < n and _UNIT_RE.match(lines[j]):
                if not unit:
                    unit = lines[j].strip()
                j2 = j + 1
                if j2 < n and _RANGE_RE.match(lines[j2]):
                    ref_low, ref_high = _parse_ref(lines[j2])
            if m2:
                row = {"item_name": prev.lstrip("★*＊"), "result": m2.group(1), "unit": unit,
                       "ref_low": ref_low, "ref_high": ref_high}
                out.append(row)
            else:
                out.append({"item_name": prev.lstrip("★*＊"), "result": ln, "unit": unit,
                            "ref_low": None, "ref_high": None})
            i += 1
            continue
        i += 1
    # 层3校验: 名称+结果齐全; 去重(名称,结果)
    seen = set()
    deduped = []
    for row in out:
        key = (row["item_name"], row["result"])
        if key in seen:
            continue
        seen.add(key)
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
    r"^(项目名称|指标名称|检查项目|检验项目|检查内容|检查项目名称|测定项目)$"
)
_COL_HEADER_CELL_RE = re.compile(
    r"^(检查结果|结果|测定值|单位|参考范围|参考值|正常值|提示|标志|临床意义)$"
)
_FLAG_ABNORMAL_RE = re.compile(
    r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|增高↑|升高↑|降低↓|偏低↓)$"
)
_FLAG_NORMAL_RE = re.compile(r"^(正常|未见异常|无异常|阴性|弱阳性|±|-)$")
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
        if re.match(r"^[<>~～]?\s*[\d.]+", b):
            # 数字类: 首个(含 > 前缀值如 "> 1000")为 result, 其后为 ref
            if not row["result"]:
                # 2026-08-31: 保留 >/< 符号(乙肝表面抗体 ">1000" 前端需显示符号);
                # ~～ 开头视为单限 ref 风格, 不当作 result 符号
                m = re.match(r"^([<>]?)\s*([\d.]+)", b)
                row["result"] = (m.group(1) or "") + m.group(2)
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
    """列式表格解析: 表头序列识别 → 块聚合。返回含 signal_flag 的行。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: list[dict] = []
    i, n = 0, len(lines)
    while i < n:
        if not _COL_HEADER_START_RE.match(lines[i]):
            i += 1
            continue
        # 收集表头单元格(连续 2-6 行)
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
        # 块聚合: 表头后到下一表头前的"名称行+后续行"块(签名行跳过, 贵港表头后紧跟签名)
        k = j
        while k < n:
            ln = lines[k]
            if _COL_HEADER_START_RE.match(ln):
                break
            if _SIGN_LINE_RE.search(ln):
                k += 1
                continue
            if _NAME_RE.match(ln) and not _SKIP_NAMES.match(ln) and not _is_value_line(ln):
                name = ln.lstrip("★*＊").strip()
                block: list[str] = []
                m2 = k + 1
                while m2 < n and len(block) < 5:
                    b = lines[m2]
                    if _COL_HEADER_START_RE.match(b):
                        break
                    if _NAME_RE.match(b) and not _SKIP_NAMES.match(b) and not _is_value_line(b):
                        break
                    if _SIGN_LINE_RE.search(b):
                        m2 += 1
                        continue
                    if (_UNIT_RE.match(b) or _RANGE_RE.match(b) or _is_value_line(b)
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
    # 去重
    seen: set = set()
    deduped = []
    for row in out:
        key = (row["item_name"], row["result"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


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


def _pair_from_lines(lines: list[str], idx: int, signal: str) -> Optional[dict]:
    """从信号行 idx 上下文中配对(名称, 结果): 信号行本身或其前后行。

    场景:
      - 行内: "尿酸 431 ↑" / "腰围 85 cm"(全红行)
      - 值行: "431\n↑"(信号行是值/箭头行, 向上找名称行, 向下找单位)
      - 通道3综述行: "【血脂四项】:(1)低密度脂蛋白增高4.73" → 名称=低密度脂蛋白 值=4.73
    """
    ln = lines[idx]
    # 通道3: 综述行内解析("【检查项】:(n)名称 异常词 数值")
    if signal == "word":
        rest = re.sub(r"^【[^】]*】[:：]?", "", ln)
        rest = re.sub(r"^[（(]\d+[)）]|^\d+[、.．]", "", rest).strip()
        m = re.match(
            r"^([\u4e00-\u9fa5A-Za-z()（）%·\-/ ]{1,20}?)"
            r"(增高|升高|偏高|降低|偏低|阳性|弱阳性|超重|肥胖|偏轻|偏重|增快|过重)[:：]?\s*([\d.]+)",
            rest,
        )
        if m and not _SKIP_NAMES.match(m.group(1)):
            return {"item_name": m.group(1).strip(), "result": m.group(3), "unit": "", "signal": signal}
    # 信号行行内解析: 支持编号前缀 + 【】前缀 + 名称紧跟数字(崇左综述"(1)超重25.7 BMI")
    m = None
    for variant in (lines[idx], re.sub(r"【[^】]*】[:：]?", "", lines[idx], count=1)):
        m = re.match(
            r"^(?:[（(]\d+[)）、.。]?)?([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·\-/ ]{0,20}?)[\s:：]*([\d.]+)",
            variant,
        )
        if m:
            break
    if m and not _SKIP_NAMES.match(m.group(1)):
        name = re.sub(r"(增高|升高|偏高|降低|偏低|异常)$", "", m.group(1)).strip()
        return {"item_name": name, "result": m.group(2), "unit": "", "signal": signal}
    # 向上找名称行(≤3 行内, 跳过箭头/单位/范围/序号行)
    for k in range(1, 4):
        if idx - k < 0:
            break
        cand = lines[idx - k]
        if _is_value_line(cand) or _UNIT_RE.match(cand) or _RANGE_RE.match(cand) or _ARROW_RE.match(cand):
            continue
        if _NAME_RE.match(cand) and not _SKIP_NAMES.match(cand):
            name = cand.lstrip("★*＊")
            # 向下找值行
            for j in range(idx - k + 1, min(idx + 3, len(lines))):
                v = lines[j]
                m2 = _VALUE_RE.match(v)
                if m2:
                    return {"item_name": name, "result": m2.group(1), "unit": m2.group(2).strip(), "signal": signal}
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
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue
                if re.search(r"(弃检|未检|放弃|拒检|无法完成)", text):
                    continue
                all_lines.append(text)
                idx = len(all_lines) - 1
                if any(_is_red_color(s.get("color", 0)) for s in spans):
                    signal_idx.append((idx, "red"))
                elif "↑" in text or "↓" in text:
                    signal_idx.append((idx, "arrow"))
                elif _ABNORMAL_WORD_RE.search(text) and any(ch.isdigit() for ch in text):
                    if _ABNORMAL_WORD_SKIP.search(text):
                        continue
                    # "未见异常/无明显异常"是正常表述, 不是异常信号
                    if re.search(r"(未见异常|无明显异常|未见明显异常|未见异常回声)", text):
                        continue
                    signal_idx.append((idx, "word"))
    doc.close()

    seen = set()
    out = []
    for idx, sig in signal_idx:
        row = _pair_from_lines(all_lines, idx, sig)
        # result 为空 = 弃检/未检/配对失败, 不是异常信号, 剔除
        if row and row["item_name"] and row["result"]:
            key = (row["item_name"], row["result"])
            if key not in seen:
                seen.add(key)
                out.append(row)
    return out
