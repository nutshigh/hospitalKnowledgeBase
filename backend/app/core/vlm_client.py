import html
import logging
import math
import re
import time
from typing import Optional
from httpx import Client, Timeout
from app.config import settings

logger = logging.getLogger(__name__)


class OcrUnavailableError(RuntimeError):
    """OCR 服务整体不可用(崩溃/重启窗口): 单页重试后仍大面积失败。

    调用方(parsing worker)据此记 failed_stage=ocr_failed(可重试), 而不是
    静默产出缺页的报告。单页偶发失败(已知个别扫描页不稳定的历史行为)不触发。
    """


def _ocr_outage(attempted: int, failed: int) -> bool:
    """整体故障判定: 尝试 >=2 页且 (全部失败 或 失败数 >= max(MIN_FAILS, 一半))。"""
    if attempted < 2 or failed <= 0:
        return False
    if failed == attempted:
        return True
    return failed >= max(settings.OCR_OUTAGE_MIN_FAILS,
                         math.ceil(attempted * settings.OCR_OUTAGE_FAIL_RATIO))

OCR_PROMPT = """<image>\n<|grounding|>Extract all lab test indicators from this medical report as a Markdown table.

## Personal Info
**Name:** <patient name>
**Gender:** <male/female>
**Age:** <number>
**Date:** <exam date>

## Indicators Table
| 项目名称 | 结果 | 单位 | 参考范围低 | 参考范围高 |
| --- | --- | --- | --- | --- |
| (each indicator) | (result value) | (unit) | (ref_low) | (ref_high) |

Rules:
1. Reference range "3.5-9.5": split into ref_low="3.5", ref_high="9.5"
2. "<5.0": ref_high="5.0", ref_low empty
3. ">1.0": ref_low="1.0", ref_high empty
4. Null fields: leave cell empty
5. Output EXACTLY one indicators table, do NOT repeat rows
6. Keep result values exactly as shown in the report"""


def _clean_markdown(text: str) -> str:
    """Strip special tokens and truncate hallucinated repetition."""
    text = text.replace("<｜end▁of▁sentence｜>", "")
    text = re.sub(r"<\|ref\|>.*?<\|/ref\|><\|det\|>.*?<\|/det\|>", "", text)
    text = re.sub(r"<\|ref\|>.*?<\|/ref\|>", "", text)
    text = re.sub(r"<\|det\|>.*?<\|/det\|>", "", text)
    text = text.replace("\\coloneqq", ":=").replace("\\eqqcolon", "=:")
    # Truncate at first sign of hallucination (repeated non-table lines)
    lines = text.split("\n")
    clean_lines = []
    repeat_count = 0
    for line in lines:
        stripped = line.strip()
        # Detect repeated non-table content
        if stripped and not stripped.startswith("|"):
            if clean_lines and stripped == clean_lines[-1].strip():
                repeat_count += 1
                if repeat_count >= 3:
                    break
            else:
                repeat_count = 0
        elif stripped.startswith("|"):
            repeat_count = 0
        clean_lines.append(line)
    text = "\n".join(clean_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 2026-09-14: VLM 把报告方异常标记渲染成 LaTeX/符号前缀("$ ^{*} $收缩压"/"* 耳（右）",
# 六院金山) —— 名称前的标记不属于指标名, 统一剥除。
# 2026-09-15: 补 ☆(尿检异常标记, 蔡超)/★, 并规范化名称里的希腊字母 LaTeX。
_VLM_NAME_MARKER_RE = re.compile(
    r"^\s*(?:\$\s*\^\s*\{\s*\*\s*\}\s*\$|\$\s*\*?\s*\$|\^\{?\*\}?|\*|＊|★|☆|✦|▲|△|\s)+")
_GREEK_MAP = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "mu": "μ",
              "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ", "Mu": "Μ"}


def _strip_vlm_name_marker(name: str) -> str:
    s = _VLM_NAME_MARKER_RE.sub("", name or "").strip()
    s = _normalize_math_text(s)
    for en, ch in _GREEK_MAP.items():
        s = s.replace("\\" + en, ch)
    # 2026-09-24 用户口径修正: "微白蛋白"与"尿微量白蛋白"是**两项不同指标**
    # (微白蛋白是尿检中的项目), 不做名称合并 —— 上一版别名已回滚。
    return s


# 2026-09-15: OCR 把单位渲染成 LaTeX/数学符("$ 10^{9}/L $"/"$ \mu $g/L"/
# "mL/(min*1.73m^{2})") → 去掉 $ 包裹与 {} , \mu→μ 等(东方安鹏)
_MATH_WRAP_RE = re.compile(r"\$\s*([^$]*?)\s*\$")


def _normalize_math_text(s):
    if not s:
        return s
    s = str(s)
    s = _MATH_WRAP_RE.sub(r"\1", s)
    s = s.replace("$", "")
    s = (s.replace("\\mu", "μ").replace("\\times", "×")
         .replace("\\cdot", "·").replace("\\uparrow", "").replace("\\downarrow", ""))
    s = re.sub(r"\^\s*\{\s*([^}]*?)\s*\}", r"^\1", s)
    s = s.replace("{", "").replace("}", "")
    return s.strip()


def _norm_flag_cell(cell) -> str:
    """标志/提示单元格归一(2026-09-23): 剥 $ 包裹/空格, LaTeX 箭头 → unicode。

    OCR 扫描件标志列常输出 "$ \\uparrow $" / "\\uparrow"; 旧判定要求整格恰为
    "\\uparrow" 而失配(鲍海宽胆红素三项标志丢失)。
    """
    s = str(cell or "").strip().replace("$", "")
    # 2026-10-13: LaTeX 星号("^{*}"/"\*")归一为 "*"(红星医院 标志列 "*" = 异常标志)
    s = re.sub(r"\^?\{?\*\}?", "*", s).replace("\\*", "*")
    return (s.replace("\\uparrow", "↑").replace("\\downarrow", "↓").strip())


def _parse_personal_info(text: str) -> dict:
    """Extract personal info from Markdown/text lines like **Key:** value or Key: value."""
    info = {}
    patterns = {
        "name": r"(?:\*\*Name:\*\*|Name)\s*[:：]\s*(.+?)(?:\s+(?:Gender|性\s*别)|$)",
        "gender": r"(?:\*\*Gender:\*\*|Gender)\s*[:：]\s*(.+?)(?:\s+(?:Age|年\s*龄)|$)",
        "age": r"(?:\*\*Age:\*\*|Age)\s*[:：]\s*(\d+)",
        "check_date": r"(?:\*\*Date:\*\*|Date)\s*[:：]\s*(.+?)(?:\n|$)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and val.lower() not in ("<patient name>", "<male/female>", "<number>", "<exam date>", "null", "none"):
                info[key] = val
    return info


def _parse_personal_info_cn(text: str) -> dict:
    """从中文体检报告 markdown 文本提取个人信息。

    匹配常见格式：姓名:XXX / 姓名 XXX、性别:男、年龄:30岁、体检日期:2024-01-01、
    机构名:XX医院 / XX医院健康管理中心
    """
    info = {}
    # 2026-09-14: 双语标签("姓名(Name)：___ 陈磊"/"体检日期(Date of Check Up): 2026-07-03")
    # —— 允许键后括号英文与下划线占位; 姓名只取汉字(避免捕获 "___")
    patterns = {
        "name": r"姓\s*名(?:\([^)]*\))?[:：\s_]*([\u4e00-\u9fa5·]{2,10})",
        "gender": r"性\s*别(?:\([^)]*\))?[:：\s_]*(男|女)",
        "age": r"年\s*龄(?:\([^)]*\))?[:：\s_]*(\d+)",
        "check_date": r"(?:体检日期|检查日期|日期|日\s*期)(?:\([^)]*\))?[:：\s_]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})",
        # === STRATEGY:v2026-08-04-unitname 提取体检机构名 ===
        # 匹配 "XX医院" / "XX医院健康管理中心" / "XX体检中心"，取医院名部分
        "unit_name": r"([\u4e00-\u9fa5A-Za-z]{2,30}?(?:医院|体检中心|健康管理中心|中心医院))",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            val = m.group(1).strip()
            if val:
                info[key] = val
    # 机构名规范化：去掉"健康管理中心"等后缀，只保留医院名
    if info.get("unit_name"):
        unit = info["unit_name"]
        # "xxx医院健康管理中心" → "xxx医院"；"xxx体检中心" → "xxx体检中心"
        m2 = re.search(r"(.+?(?:医院|体检中心|中心医院))", unit)
        if m2:
            info["unit_name"] = m2.group(1)
    return info


def _html_table_rows(text: str) -> list[list[str]]:
    """从 PaddleOCR-VL 输出的 HTML <table> 提取行单元格(PaddleOCR-VL 默认输出 HTML 表格而非 markdown |)。"""
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", text, re.S):
        cells = []
        for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S):
            # 2026-09-15: 还原 HTML 实体("&gt;1.04"/"&lt;1.70"/"≤"等) —— 否则参考值
            # 带着 "&gt;" 字符串入库, 前端显示成 ">…"(东方安鹏)
            cells.append(html.unescape(re.sub(r"<[^>]+>", "", td)).strip())
        rows.append(cells)
    return rows


def _parse_markdown_table(text: str) -> list[dict]:
    """Parse a Markdown/HTML table into a list of indicator dicts.

    2026-09-23: 按 **<table> 块逐表**解析 —— 同页多表(双栏并排/连续多表)此前被
    拉平成一个表, 表头与列映射跨表串用 → 标志/结果错配(陈海波双栏表整页错乱、
    薄楠"阴道加德纳菌/抗甲状腺球蛋白抗体"假黄)。markdown 表按连续 | 行分块。
    """
    out: list[dict] = []
    if "<tr>" in text:
        blocks = re.findall(r"<table[^>]*>(.*?)</table>", text, re.S)
        _last_header: list = []
        _ctx = ""
        for b in (blocks or [text]):
            rows = _html_table_rows(b)
            if _is_history_table(rows):
                continue  # 历年对比表整表跳过(历史值不是本次指标)
            # 2026-10-13: 表标题跟踪(尿常规/粪便常规) —— 裸"隐血"按所属表命名
            _head_txt = " ".join(str(c) for r in rows[:1] for c in r)
            if re.search(r"尿常规|尿液|尿沉渣", _head_txt):
                _ctx = "urine"
            elif re.search(r"粪便|大便|粪常规|便常规", _head_txt):
                _ctx = "stool"
            # 2026-10-13: 跨页续表补表头 —— OCR 在页边界把一张表拆成两段时, 后段
            # 无表头行(胡月 白带常规+BV 跨页: 后段"清洁度 III度↑"行整表被
            # _rows_to_indicators 丢弃) → 无表头段沿用上一段表头再解析。
            _has_head = any(_match_header_columns(r or []) for r in rows[:2])
            if _has_head:
                _last_header = next(
                    (r for r in rows[:2] if _match_header_columns(r or [])), [])
            elif _last_header and rows:
                rows = [list(_last_header)] + rows
            _parsed = _rows_to_indicators(rows)
            if _ctx in ("urine", "stool"):
                for _p in _parsed:
                    if (_p.get("item_name") or "").strip() in ("隐血", "潜血"):
                        _p["item_name"] = ("尿隐血" if _ctx == "urine"
                                           else "粪便隐血")
            out.extend(_parsed)
        return out
    cur: list[list[str]] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [html.unescape(c.strip()) for c in stripped[1:-1].split("|")]
            if all(c.replace("-", "").replace(" ", "") == "" for c in cells):
                continue  # separator row
            cur.append(cells)
        else:
            if cur:
                out.extend(_rows_to_indicators(cur))
                cur = []
    if cur:
        out.extend(_rows_to_indicators(cur))
    return out


def _is_history_table(table_rows: list) -> bool:
    """2026-09-24: 历年对比表判定 —— 表头(前两行)含 ≥2 个日期列。

    陈海波 p22 历年对比表("项目|2026-05-29|2024-04-19|2023-04-20")的"微白蛋白：>150
    (0-100mg/L)"被当指标且值错误; 文本路径已有 _strip_history_compare_table, VLM 路径补同款。
    """
    if not table_rows:
        return False
    head = " ".join(str(c) for c in table_rows[0])
    if len(re.findall(r"20\d{2}\s*[-/.年]\s*\d{1,2}", head)) >= 2:
        return True
    if len(table_rows) > 1:
        head2 = " ".join(str(c) for c in table_rows[1])
        if len(re.findall(r"20\d{2}\s*[-/.年]\s*\d{1,2}", head2)) >= 2:
            return True
    return False


def _rows_to_indicators(table_rows: list) -> list[dict]:
    """单表行集 → 指标列表(表头检测 + 列映射 + 小结行兜底)。"""
    if len(table_rows) < 2:
        return []

    # 2026-08-26: 表头行不一定是第一行(钦州等报告的"模块标题行"先出现)
    # 找第一个能映射出字段的表头行; 其后的"检查人员/审核时间"类行跳过
    header_idx = None
    for i, row in enumerate(table_rows):
        if _match_header_columns(row):
            header_idx = i
            break
    if header_idx is None:
        return []

    # Map header columns
    header = table_rows[header_idx]
    col_map = _match_header_columns(header)

    def _period_of(_hdr: list, _cmap: dict) -> int:
        """表头角色序列的最小重复周期(单表双栏/多栏, 陈海波"分析项目|结果|参考值|单位"×2)。"""
        roles = [_cmap.get(i) for i in range(len(_hdr))]
        for _p in range(1, len(_hdr)):
            if len(_hdr) % _p == 0 and all(roles[i] == roles[i % _p] for i in range(len(_hdr))):
                return _p
        return len(_hdr)

    period = _period_of(header, col_map)
    # 2026-10-07: 双栏且表头不对称(蔡镇宇"项目|结果||项目|结果|参考值|提示|单位" ——
    # 左半缺列)时 period 判不出 → 行内按**名称列位置**切段独立解析(整行解析时左半
    # 被右半覆盖丢失: 身高/血压（收缩压）); 未映射列上的提示词按标志处理
    _name_cols = [i for i in sorted(col_map) if col_map.get(i) == "item_name"]
    _dual_holder = [len(_name_cols) >= 2]

    indicators = []
    seen = set()

    def _process(_row: list, _cmap: dict, _period: int) -> None:
        indicator = _row_to_indicator(_row, _cmap, allow_unmapped_flag=bool(_dual_holder[0]))
        name = _strip_vlm_name_marker(indicator.get("item_name", "").strip())
        indicator["item_name"] = name
        if not name or name in ("(each indicator)", "项目名称", "结果"):
            return
        # 2026-10-07: 纯单位 token 误当名称(蔡镇宇 OCR 行错位 "mmHg|血压（舒张压）|103"
        # / "ng/ml|总前列腺特异性抗原|0.77") —— 段首单位 = 整段右旋一格(左半单位
        # 溢出/OCR 行错位), 角色序列右旋重排后重解; 仍为单位 token 才丢弃
        _unit_tok = (
            r"(?i:mmHg|cm|mm|kg|mg|g|L|mL|dL|uL|μL|ng/mL|ng/dl|U/L|IU/mL|mIU/mL|"
            r"mmol/L|μmol/L|umol/L|nmol/L|pmol/L|g/L|mg/L|mg/dL|10\^?\d+/L|"
            r"/HP|个/HP|个|%|s|次/分|\d+\^?\d*/L)")
        if re.fullmatch(_unit_tok, name) and len(_row) >= 3:
            _roles = [_cmap.get(i) for i in range(len(_row))]
            _row2 = _row[1:]
            _cmap2 = {_i: _roles[_i] for _i in range(len(_row2))}
            indicator = _row_to_indicator(_row2, _cmap2,
                                          allow_unmapped_flag=bool(_dual_holder[0]))
            name = _strip_vlm_name_marker(indicator.get("item_name", "").strip())
            indicator["item_name"] = name
            if not name or name in ("(each indicator)", "项目名称", "结果") \
                    or re.fullmatch(_unit_tok, name):
                return
        if re.search(r"(姓名[:：]|性别[:：]|门诊号|体检编号|样本编号|既往病史|家族病史|月经史|"
                     r"遗传病史|过敏史|婚育史|手术史|输血史|神经及精神疾病|科\s*室|单位名称|单位备注)", name):
            return
        if not indicator.get("result") and not (indicator.get("ref_low") or indicator.get("ref_high")):
            return
        # 2026-10-07(用户口径): 结果"弱阳性" + **与参考范围不一致** + 本表**无提示/
        # 标志列** → 置 flag=1(run_rules: flag=1 无数值参考 → 判黄)。
        #  - 有提示列且未标 → 绿(池州粘液丝等用户拍板案例);
        #  - 参考列缺失 / 参考本身含"弱/阳性" → 无法认定不一致 → 不判
        #  (幽门螺杆菌分型抗体表 序号|项目|缩写|结果|单位|参考, 参考"阴性"+结果"弱阳性")
        if "弱阳性" in str(indicator.get("result") or "") \
                and "flag" not in set(v for v in _cmap.values() if v):
            _ref_txt = "".join(
                str(_row[_i]) for _i in range(len(_row))
                if _cmap.get(_i) in ("ref_low", "ref_high")).replace(" ", "")
            if _ref_txt and "阳性" not in _ref_txt \
                    and ("阴性" in _ref_txt or _ref_txt in ("-", "--")
                         or "negative" in _ref_txt.lower()):
                indicator["signal_flag"] = max(indicator.get("signal_flag") or 0, 1)
        _res = str(indicator.get("result") or "")
        if len(_res) > 50 or re.search(r"[。；！？]", _res):
            return
        # 2026-09-24: 叙述式结果单元格("微白蛋白：>150（0-100mg/L）", 历年对比/汇总表)
        # → 不入指标(陈海波 p22 历年对比表残留; 头部日期列偶被 OCR 读乱时兜底)
        if ("：" in _res or ":" in _res) and name and name in _res:
            return
        key = (name, indicator.get("result", ""))
        if key in seen:
            return
        seen.add(key)
        if any(re.search(r"[\u4e00-\u9fa5]", str(indicator.get(k) or ""))
               for k in ("ref_low", "ref_high")):
            indicator["ref_low"] = None
            indicator["ref_high"] = None
        else:
            for ref_key in ("ref_low", "ref_high"):
                val = indicator.get(ref_key)
                if val is None:
                    continue
                sv = str(val)
                if not re.search(r"\d", sv):
                    indicator[ref_key] = None
                    continue
                lo, hi = _parse_ref_range(sv)
                if lo is not None or hi is not None:
                    indicator["ref_low"] = lo
                    indicator["ref_high"] = hi
        _apply_numeric_fallback(indicator)
        indicators.append(indicator)

    for row in table_rows[header_idx + 1:]:
        if any(("人员" in c) or ("检查时间" in c) or ("审核时间" in c) or ("时间:" in c) for c in row):
            continue
        # 2026-08-26: 多表格报告(钦州)后续模块自带表头 → 切换列映射
        cm = _match_header_columns(row)
        if cm:
            col_map = cm
            period = _period_of(row, cm)
            _name_cols = [i for i in sorted(col_map) if col_map.get(i) == "item_name"]
            _dual_holder[0] = len(_name_cols) >= 2
            continue
        # 2026-10-07: 双栏表头不对称(period 撑满整行) → 按名称列切段
        if (_dual_holder[0] and period >= len(row)
                and len(_name_cols) >= 2 and len(row) >= len(_name_cols) + 2):
            _bounds = _name_cols + [len(row)]
            for _a, _b in zip(_bounds, _bounds[1:]):
                if _b - _a < 2:
                    continue
                _sub = row[_a:_b]
                _submap = {i - _a: col_map.get(i) for i in range(_a, _b)}
                _submap[0] = "item_name"
                _process(_sub, _submap, len(_sub))
            continue
        # 2026-09-23: 双栏/多栏行按周期拆成多个子行分别解析(否则两组数据互相错配)
        if period and period < len(row):
            for _i in range(0, len(row), period):
                _sub = row[_i:_i + period]
                if len(_sub) < 2:
                    continue
                _process(_sub, {k: col_map.get(k) for k in range(period)}, period)
        else:
            _process(row, col_map, period)

    _apply_summary_row_flags(indicators, table_rows)
    return indicators


def _apply_summary_row_flags(indicators: list, table_rows: list) -> None:
    """2026-09-23: 表尾"本次小结/小结"行补标志。

    扫描件 OCR 偶丢提示列(薄楠尿常规表 P/Q 列整列缺失) → 从表尾小结行
    "尿胆红素（BIL）阳性[1+ …]；尿胆原（URO）弱阳性[…]" 给对应指标补
    signal_flag=3, 避免异常项判绿。
    """
    if not indicators or not table_rows:
        return
    for row in table_rows:
        if not row:
            continue
        head = str(row[0] or "").strip()
        if not re.search(r"(本次小结|小结|检查结论|结论)", head):
            continue
        text = "".join(str(c or "") for c in row[1:])
        for seg in re.split(r"[；;]", text):
            m = re.match(
                r"^\s*([\u4e00-\u9fa5A-Za-z0-9()（）/·\-]+?)\s*"
                r"(阳性|弱阳性|异常|偏高|偏低|升高|降低|↑|↓)", seg)
            if not m:
                continue
            probe = re.sub(r"[\s（）()]", "", m.group(1))
            if len(probe) < 2:
                continue
            for ind in indicators:
                n = re.sub(r"[\s（）()]", "", str(ind.get("item_name") or ""))
                if n and (n == probe or probe in n or n in probe):
                    ind["signal_flag"] = ind.get("signal_flag") or 3
                    break


def _match_header_columns(headers: list[str]) -> dict:
    """Match Chinese header keywords to standard field names."""
    keywords = {
        "item_name": ["项目名称", "检验项目", "项目", "指标名称", "检查项目", "测定项目"],
        "item_code": ["缩写", "英文简称", "代码", "代号", "缩写符号"],
        "result": ["结果", "测定值", "检验结果", "实测值", "检测结果", "数值"],
        "unit": ["单位", "计量单位"],
        "ref_low": ["参考范围低", "参考低", "下限"],
        "ref_high": ["参考范围高", "参考高", "上限"],
        # 2026-08-29: 提示/标志列(广西"提示"列: ↑/偏高/H 等报告方异常标志)
        "flag": ["提示", "标志", "结果提示"],
    }

    def _jaccard(a: str, b: str) -> float:
        sa, sb = set(a), set(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    mapping = {}
    for i, h in enumerate(headers):
        # 2026-09-14: 值/否定描述单元格("未提示"/"未见异常"/"无")不是表头词 ——
        # 否则数据行 ["红细胞信息","未提示",…] 被当表头切换列映射, 后续指标漏提
        # (仁济陈磊尿液分析"粘丝")。
        if h.startswith(("未", "无", "不", "非")):
            continue
        # 2026-09-15: "上次结果/历史结果"列不映射 —— 否则空的上次列会覆盖
        # "本次结果"值, 导致所有结果丢失(东方安鹏双值表)
        if "上次" in h or "历史" in h:
            continue
        best_key, best_score = None, 0.0
        for key, aliases in keywords.items():
            for alias in aliases:
                score = _jaccard(h, alias)
                if score > best_score:
                    best_score = score
                    best_key = key
        if best_score > 0.3:
            mapping[i] = best_key

    return mapping


def _row_to_indicator(row: list[str], col_map: dict,
                      allow_unmapped_flag: bool = False) -> dict:
    """Convert a table row to an indicator dict using column mapping.

    allow_unmapped_flag: 双栏表(表头含 ≥2 个"项目"列)时, 未映射列上的提示词
    (蔡镇宇双栏表左半缺表头 —— 提示"偏高"列无角色映射, 曾整列丢失)按标志处理;
    单栏表保持关闭(薄楠历史结果列"阳性"曾被当标志假黄)。"""
    indicator = {}
    row = list(row)
    # 2026-08-29: 任意列出现报告方异常标志(提示列值/错位列) → signal_flag=3
    # (钦州 YMII 行列错位, "↑"落在参考列; 统一按标志词扫描兜底)
    for _ci, cell in enumerate(row):
        # 2026-09-23: 先归一 OCR 数学包裹("$ \\uparrow $" → "↑")再判 —— 鲍海宽"标志"列;
        # 仅扫**已映射列**(未映射列多为历史结果列, 薄楠加德纳菌 2024 列"阳性"曾被当标志
        # 假黄); 名称列不扫。钦州 YMII 错位场景的箭头落在参考列(已映射)仍可兜住。
        if _ci not in col_map or col_map.get(_ci) == "item_name":
            continue
        _c = _norm_flag_cell(cell)
        if re.match(r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|\*)$", _c):
            indicator["signal_flag"] = 3
            break
    # 2026-09-14: 箭头附着在结果单元格尾部("14.0 \uparrow"/"0.59\downarrow"/"14.0↑")
    # → 置 signal_flag=3 并从单元格剥离(扫描件 VLM 常把箭头并入结果/参考列)
    _arrow_tail = re.compile(r"\s*(?:\\uparrow|\\downarrow|↑|↓)\s*$")
    for i, cell in enumerate(row):
        # 2026-09-23: 先剥 $ 包裹再判尾箭头(陈海波结果列 "$ 14.0 \\uparrow $" 曾漏配)
        _c = cell.strip().replace("$", "").strip()
        if _arrow_tail.search(_c):
            indicator["signal_flag"] = 3
            row[i] = _arrow_tail.sub("", _c).strip()
    for i, cell in enumerate(row):
        key = col_map.get(i)
        if key is None:
            if allow_unmapped_flag and re.match(
                    r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|\*)$",
                    _norm_flag_cell(cell)):
                indicator["signal_flag"] = 3
                continue
            # Guess: number → result, contains "-" → ref_range
            if re.match(r"^[\d.]+$", cell) and "result" not in indicator:
                key = "result"
            elif re.match(r"^[<＞>\d].*[\d]|[～\-—].*[\d]", cell):
                if "ref_low" not in indicator:
                    low, high = _parse_ref_range(cell)
                    indicator["ref_low"] = low
                    indicator["ref_high"] = high
                continue
            else:
                continue

        if key == "item_name":
            indicator["item_name"] = cell
        elif key == "item_code":
            indicator["item_code"] = cell
        elif key == "result":
            # 2026-09-24: 结果里的 LaTeX 符号归一(曾社健"弱阳性（ \\pm ）" → "弱阳性（±）")
            cell = (cell.replace("\\pm", "±").replace("\\mu", "μ")
                    .replace("\\times", "×").replace("\\cdot", "·"))
            indicator["result"] = cell
        elif key == "unit":
            cell = _normalize_math_text(cell.strip())
            # 2026-08-26: 无单位行(体重指数等)参考范围可能被 VLM 对齐到单位列
            # 2026-10-13: 等级区间("I度～II度", 胡月白带清洁度续表参考列错落单位列)
            # 非数值参考 → 用户口径"可以不显示", 丢弃
            if re.search(r"\d[\d.]*\s*[~\-—～到至]\s*[\d.]+", cell) or re.match(r"[<>＜＞~～]\s*[\d.]+", cell):
                lo, hi = _parse_ref_range(cell)
                indicator["ref_low"] = lo
                indicator["ref_high"] = hi
            elif re.fullmatch(
                    r"(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|[IV]+)\s*度?\s*[~～\-—]\s*"
                    r"(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|[IV]+)\s*度?", cell):
                pass
            else:
                indicator["unit"] = cell
        elif key == "flag":
            # 2026-08-29: 提示/标志列 → 报告方异常标志(↑/↓/偏高/H/L/阳性/LaTeX \uparrow) → signal_flag=3
            # 2026-09-23: 先归一数学包裹("$ \uparrow $" → "↑")
            cell = _norm_flag_cell(cell)
            if re.match(r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|\*)$", cell):
                indicator["signal_flag"] = 3
        elif key == "ref_low":
            indicator["ref_low"] = cell or None
        elif key == "ref_high":
            indicator["ref_high"] = cell or None

    # 2026-09-24: 分级定性参考("阴性:<0.9 可疑:0.9--1.1 阳性:≥1.1")且数值落在**阴性带**
    # → 清掉标志(OCR 提示列列错位误读; 薄楠 EB病毒早期抗原IgM抗体 0.05 被读成 ↑)。
    # 用户口径(2026-09-24): 保留该行, 只清误读标志。
    if indicator.get("signal_flag"):
        _rv0 = _as_float(indicator.get("result"))
        if _rv0 is not None:
            _refs0 = " ".join(str(c or "") for c in row)
            _m0 = re.search(
                r"阴性\s*[:：]?\s*(?:[<≤]\s*)?([\d.]+)(?:\s*[-~—到至]\s*([\d.]+))?", _refs0)
            if _m0:
                _hi0 = float(_m0.group(2) or _m0.group(1))
                if _rv0 < _hi0:
                    indicator["signal_flag"] = 0

    # 2026-09-15: 数值越界兜底(箭头等价)统一在 _rows_to_indicators 的 ref 拆分后执行。
    # 2026-09-23: 此处曾先跑一次 —— 单侧上限参考("<2.7")被 _as_float 当**下限** 2.7,
    # 导致正常值误判越界(陈海波 甘胆酸/总胆固醇/甘油三酯/LDL/总胆汁酸/肌酸激酶 假黄)
    # → 移除, 只保留拆分后的调用。
    return indicator


def _apply_numeric_fallback(indicator: dict) -> None:
    """无标志且结果/参考均为数值且越界 → signal_flag=2(箭头等价, 扫描件专用)。"""
    if indicator.get("signal_flag") or indicator.get("result") is None:
        return
    rv = _as_float(indicator.get("result"))
    lo = _as_float(indicator.get("ref_low"))
    hi = _as_float(indicator.get("ref_high"))
    if rv is not None and ((lo is not None and rv < lo)
                           or (hi is not None and rv > hi)):
        indicator["signal_flag"] = 2


def _as_float(v):
    """数值化(容忍 >/</*/单位混排符号); 非数值返回 None。"""
    if v is None:
        return None
    s = str(v).strip().lstrip("<>≤≥＞＜*=＜* ")
    import re as _re
    m = _re.match(r"^[\d.]+", s)
    try:
        return float(m.group(0)) if m else None
    except ValueError:
        return None


def _parse_ref_range(text: str) -> tuple:
    """Parse reference range string. "3.5-9.5" → ("3.5", "9.5")."""
    text = text.strip()
    # 2026-09-15: 双横线范围 "40--50"(安鹏) 与单横线一并支持
    m = re.match(r"([\d.]+)\s*[-~—到至]{1,2}\s*([\d.]+)", text)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"[<＜≤]\s*([\d.]+)", text)
    if m:
        return None, m.group(1)
    m = re.match(r"[>＞≥]\s*([\d.]+)", text)
    if m:
        return m.group(1), None
    # 2026-08-26: 钦州单限格式 "~5.17"(仅有上限) / "5.17~"(仅有下限)
    m = re.match(r"[~～]\s*([\d.]+)$", text)
    if m:
        return None, m.group(1)
    m = re.match(r"([\d.]+)\s*[~～]$", text)
    if m:
        return m.group(1), None
    return None, None


class VLMClient:
    def __init__(self, base_url: str = settings.OCR_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.model = settings.OCR_MODEL
        custom_prompt = getattr(settings, "OCR_PROMPT", None)
        self.prompt = custom_prompt.strip() if custom_prompt else OCR_PROMPT
        self.client = Client(timeout=Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0))

    def extract_from_image(self, image_base64: str) -> dict:
        """调用 PaddleOCR-VL 服务（/ocr），返回 {personal_info, indicators, raw_text}。

        PaddleOCR-VL 输出页面级 markdown（含表格），复用现有
        _parse_markdown_table / _parse_personal_info 解析。
        """
        # 2026-10-14: 单页失败重试(OCR 看门狗重启窗口内的请求靠这里吸收) ——
        # 连接错误/超时/5xx 重试 settings.OCR_CLIENT_RETRIES 次(退避 1s/3s)。
        last_exc: Optional[Exception] = None
        attempts = max(0, int(getattr(settings, "OCR_CLIENT_RETRIES", 0)))
        for i in range(attempts + 1):
            try:
                response = self.client.post(
                    f"{self.base_url}/ocr",
                    json={"image_base64": image_base64},
                )
                response.raise_for_status()
                break
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                retriable = status is None or status >= 500
                last_exc = e
                if i >= attempts or not retriable:
                    raise
                logger.warning("ocr page request failed (attempt %s/%s): %s",
                               i + 1, attempts + 1, e)
                time.sleep(1.0 + 2.0 * i)
        data = response.json()
        content = (data.get("markdown") or "").strip()
        content = _clean_markdown(content)

        personal_info = _parse_personal_info(content)
        if not personal_info:
            personal_info = _parse_personal_info_cn(content)
        indicators = _parse_markdown_table(content)

        return {"personal_info": personal_info, "indicators": indicators, "raw_text": content}

    def extract_from_images(self, images_base64: list[str]) -> dict:
        all_indicators = []
        personal_info = {}
        raw_texts = []
        failed_pages = []
        for i, img in enumerate(images_base64):
            # 2026-08-26: 单页失败跳过, 不中断整份报告
            # (PaddleOCR-VL 对个别扫描页会崩且服务不自动恢复, 保底至少拿到能识别的页)
            try:
                result = self.extract_from_image(img)
            except Exception:
                failed_pages.append(i + 1)
                continue
            if result.get("personal_info"):
                for k, v in result["personal_info"].items():
                    if v is not None:
                        personal_info[k] = v
            if result.get("indicators"):
                all_indicators.extend(result["indicators"])
            if result.get("raw_text"):
                raw_texts.append(result["raw_text"])
        if failed_pages:
            logger.warning("VLM extract_from_images failed pages: %s", failed_pages)
        # 2026-10-14: 大面积失败 = 服务级故障 → 抛 OcrUnavailableError(worker 记
        # failed_stage=ocr_failed 走重试); 个别页失败仍按历史行为跳过。
        if _ocr_outage(len(images_base64), len(failed_pages)):
            raise OcrUnavailableError(
                f"OCR service outage: {len(failed_pages)}/{len(images_base64)} pages failed")
        return {"personal_info": personal_info, "indicators": all_indicators,
                "raw_text": "\n\n".join(raw_texts)}

    def extract_conclusion(self, image_base64: str) -> str:
        """调用 PaddleOCR-VL 提取报告最后的结论/建议段落。"""
        CONCLUSION_PROMPT = """请提取这份体检报告最后的"总检建议与结论"或"医师建议""健康指导"等结论性段落的内容。

只输出该段落原文，不要表格、不要指标列表。如果没有找到，输出"NONE"。"""
        response = self.client.post(
            f"{self.base_url}/ocr",
            json={"image_base64": image_base64, "prompt": CONCLUSION_PROMPT},
        )
        response.raise_for_status()
        data = response.json()
        return (data.get("markdown") or "").strip()

    def extract_conclusion_from_images(self, images_base64: list[str]) -> Optional[str]:
        """从多页图像提取结论，取最后一页/最后几页的结果。"""
        # 结论通常在报告最后几页
        candidate_pages = images_base64[-3:] if len(images_base64) > 3 else images_base64
        texts = []
        for img in candidate_pages:
            try:
                t = self.extract_conclusion(img)
                if t and t.upper() != "NONE" and len(t) > 10:
                    # 2026-08-29: 结论输出可能混入 HTML 表格/图片标签, 剥除(仅结论路径)
                    t = re.sub(r"<table.*?</table>", "", t, flags=re.DOTALL)
                    t = re.sub(r"<[^>]+>", "", t)
                    t = re.sub(r"\n{2,}", "\n", t)
                    texts.append(t)
            except Exception:
                pass
        return "\n\n".join(texts) if texts else None


vlm_client = VLMClient()
