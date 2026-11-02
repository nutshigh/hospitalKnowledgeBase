from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.modules.szbf.normalize import normalize_deviation, normalize_item_name
from app.modules.szbf.profiles import SzbfProfile


@dataclass
class ParsedTitle:
    name: str
    kind: str  # indicator | finding
    raw: str


@dataclass
class ParsedIndicator:
    item_name: str
    result_value: str | None
    unit: str | None
    deviation: str | None
    raw: str


@dataclass
class ParsedConclusion:
    titles: list[ParsedTitle] = field(default_factory=list)
    indicators: list[ParsedIndicator] = field(default_factory=list)


_NUMBERED_RE = re.compile(
    r"^\s*(?:\d+\s*[.、．]|\d+\s*[）)]|[（(]\s*\d+\s*[）)])\s*(.+)$"
)
_BRACKET_RE = re.compile(r"【([^】]{1,60})】")
_ENUM_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")
_CLAUSE_SPLIT_RE = re.compile(r"[；;]")
_COMPOUND_SPLIT_RE = re.compile(r"、")
_MEASUREMENT_RE = re.compile(
    r"^(?:"
    r"(?:LVEF|PASP|EF)\s*\d"
    r"|(?:大小|约|范围)?\s*\d+(?:\.\d+)?(?:\s*[×xX*]\s*\d+(?:\.\d+)?)*\s*"
    r"(?:cm|mm|ml|mL|g|kg|%|次/分|次/分钟|bpm)"
    r")",
    re.I,
)
_SECTION_LABELS = (
    "医学解释", "常见原因", "健康指导", "检查建议", "解释", "说明", "科普",
    "什么是", "定义", "病因", "危害", "预防", "治疗", "随访", "注意事项",
    "温馨提示", "既往史", "病史", "健康处方",
)
_SECTION_LABEL_RE = re.compile(
    r"^(?:" + "|".join(re.escape(label) for label in _SECTION_LABELS) + r")\s*[：:]?"
)
_CN_ENUMERATOR_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+\s*[、.．]|[（(]\s*[一二三四五六七八九十]+\s*[）)])\s*"
)
_ARABIC_ENUMERATOR_RE = re.compile(
    r"^\s*(?:\d+\s*[.、．]|[（(]\s*\d+\s*[）)])\s*"
)
_LEADING_BRACKET_RE = re.compile(r"^\s*【([^】]{1,60})】\s*")
_METHOD_ONLY_TOKEN = (
    r"(?:心脏|胸部?|腹部|泌尿|妇科|甲状腺|颈动脉|乳腺|双|左|右)?"
    r"(?:彩超|超声心动图|超声骨密度检测|超声|B超|CT|胸片|X线|核磁|MRI|"
    r"动态心电图|心电图|骨密度|查体|内科|外科|眼科|口腔|耳鼻喉科?|妇科|"
    r"检验科?|眼底|体检|检查|检验|放射科)"
)
_METHOD_ONLY_RE = re.compile(
    r"^(?:\d{2,4}\s*年)?"
    + _METHOD_ONLY_TOKEN
    + r"(?:\s*[及和、]\s*" + _METHOD_ONLY_TOKEN + r")*"
    r"(?:检查|查体|检验|检测|结果|提示|结论|报告|室|科|右|左|双眼)*$"
)
_ADVICE_WORDS_RE = re.compile(
    r"(建议|请|需|注意|治疗|复查|随访|预防|病因|危害|表现|症状|由于|引起|导致"
    r"|(?<!反)(?<!适)应(?!用|激|答|变|力|急|聘|收|付|试|届|对|是)"
    r"|可能(?:是|为|由于|与|存在|出现|导致|引起|提示|加重|影响|伴有|合并))"
)
_BLOOD_PRESSURE_RE = re.compile(
    r"(?:血压|收缩压|舒张压)\s*[:：]\s*\d+(?:\.\d+)?\s*[/／]\s*\d+"
)
_TAIL_PAREN_RE = re.compile(r"\s*[（(]\s*([^（）()]*?)\s*[)）]\s*$")
_PRESERVE_TAIL_RE = re.compile(r"(左|右|双|TI-?RADS|C-?TIRADS|BI-?RADS)", re.I)
_STRIP_TAIL_RE = re.compile(
    r"^(?:"
    r"(?:轻|中|重)度?"
    r"|\d+(?:\.\d+)?\s*(?:次/分|次/分钟|bpm)"
    r")$",
    re.I,
)
_COMPLETE_FINDING_RE = re.compile(
    r"(偏高|偏低|升高|降低|增高|增多|减少|不足|异常|阳性|反流|狭窄|钙化灶?|结节|囊肿|"
    r"息肉|增生|肥大|结石|斑块|增厚|积液|扩张|脱垂|关闭不全|供血不足|缺血|梗死|"
    r"过缓|过速|不齐|早搏|阻滞|紊乱)$"
)
_INDICATOR_TERMS = (
    "尿酸", "胆固醇", "甘油三酯", "血糖", "糖化血红蛋白", "胆红素",
    "转氨酶", "转移酶", "肌酸激酶", "肌酐", "尿素", "白细胞", "红细胞",
    "血红蛋白", "血小板", "尿蛋白", "尿糖", "尿酮体", "尿潜血",
    "尿比重", "尿酸碱度", "尿胆原", "尿上皮细胞", "维生素D",
    "同型半胱氨酸", "载脂蛋白", "脂蛋白", "前列腺特异抗原",
    "甲胎蛋白", "癌胚抗原", "促甲状腺激素", "甲状腺素",
    "三碘甲状腺原氨酸", "乙肝表面抗体", "乙肝表面抗原", "乙肝e抗体",
    "乙肝e抗原", "乙肝核心抗体", "碱性磷酸酶", "胱抑素C",
    "胃蛋白酶原", "红细胞分布宽度", "平均血小板体积",
    "神经元特异性烯醇化酶", "鳞状细胞癌相关抗原", "CA-199",
)
_ABNORMAL_TAIL_RE = re.compile(r"(偏高|偏低|升高|降低|增高|不足|异常|阳性)")
_NUM_LAB_RE = re.compile(
    r"(?<![\w])\*?\s*([\u4e00-\u9fa5A-Za-z0-9()（）/·\-]+)\s*[:：]\s*"
    r"([<>≤≥]?\d+(?:\.\d+)?)(?!\d|\.\d)(?!\s*\+)(?!\s*[/／]\s*\d)\s*"
    r"([^\s↑↓(（+；;，,。、）):：]*)\s*([↑↓]?)\s*"
    r"(?:[（(]([^）)]*)[)）])?[ \t]*([↑↓]?)"
)
_QUAL_LAB_RE = re.compile(
    r"(?<![\w])\*?\s*([\u4e00-\u9fa5A-Za-z0-9()（）/·\-]+)\s*[:：]\s*"
    r"(\+-|(?:\d{1,2})?\+|阳性|阴性|弱阳性)"
)
_LAB_NAME_SKIP = {"血压", "收缩压", "舒张压"}
_BAD_INDICATOR_NAME_RE = re.compile(r"[，,；;。+]|检测异常|比值正常|常见原因|病史|本次体检|包括")
_BAD_INDICATOR_TAIL_RE = re.compile(r"(?:轻度|中度|重度)$")
_JUNK_INDICATOR_NAME_RE = re.compile(r"检测室|常见|生理性|分类|增高原因")
_JUNK_INDICATOR_NAMES = {"II比值", "17检测"}
_PURE_NUMERIC_RE = re.compile(r"^[0-9.]+$")
_FRAGMENT_PREFIX_RE = re.compile(r"^(?:[()]|[A-Za-z]+\))")
_LATIN_ONLY_RE = re.compile(r"^[A-Za-z0-9#%+\-/().]+$")
_KNOWN_LATIN_INDICATORS = frozenset(name.upper() for name in (
    "PSA", "FPSA", "NSE", "SCC", "CA-199", "CA-125", "FT3", "FT4", "TSH",
    "HBsAb", "HBsAg", "HBeAb", "HBeAg", "HBcAb", "AFP", "CEA", "CK", "LDH",
    "BUN", "CREA", "UA", "TG", "TC", "HDL", "LDL", "BMI", "PH", "SG", "WBC",
    "RBC", "PLT", "HGB", "MCV", "MCH", "MCHC", "RDW-CV", "RDW-SD", "PDW",
    "PCT", "MPV", "NEUT#", "LYM#", "MONO#", "BASO#", "EO#", "ALT", "AST",
    "GGT", "ALP", "TP", "ALB", "GLB", "TBIL", "DBIL", "IBIL", "TBA", "CHE",
    "CRP", "HCT", "P-LCR",
))


def _is_quality_indicator_name(name: str) -> bool:
    text = (name or "").strip()
    if len(text) < 2:
        return False
    if _PURE_NUMERIC_RE.fullmatch(text):
        return False
    if _FRAGMENT_PREFIX_RE.match(text):
        return False
    if _JUNK_INDICATOR_NAME_RE.search(text) or text in _JUNK_INDICATOR_NAMES:
        return False
    if _LATIN_ONLY_RE.fullmatch(text) and text.upper() not in _KNOWN_LATIN_INDICATORS:
        return False
    return True


def _is_quality_indicator_title(name: str) -> bool:
    if not name or len(name) > 30:
        return False
    if _BAD_INDICATOR_NAME_RE.search(name):
        return False
    if _BAD_INDICATOR_TAIL_RE.search(name):
        return False
    return True


def _method_re(profile: SzbfProfile) -> re.Pattern[str]:
    prefixes = sorted(profile.method_prefixes, key=len, reverse=True)
    if not prefixes:
        return re.compile(r"(?!x)x")
    alternation = "|".join(re.escape(prefix) for prefix in prefixes)
    return re.compile(rf"^(?:{alternation})(?:检查|提示|结果)?\s*[：:]\s*")


def _strip_trailing_annotation(text: str) -> str:
    match = _TAIL_PAREN_RE.search(text)
    if not match:
        return text
    inner = match.group(1)
    if _PRESERVE_TAIL_RE.search(inner):
        return text
    if _STRIP_TAIL_RE.fullmatch(inner):
        return text[: match.start()].rstrip()
    return text


def _strip_prefix(text: str, profile: SzbfProfile) -> str:
    text = text.strip().lstrip("*").strip()
    text = _ENUM_RE.sub("", text).strip()
    text = _method_re(profile).sub("", text)
    text = _strip_trailing_annotation(text)
    return text.strip()


def _title_probe(text: str) -> str:
    probe = text.strip()
    while probe:
        stripped = _CN_ENUMERATOR_RE.sub("", probe, count=1)
        stripped = _ARABIC_ENUMERATOR_RE.sub("", stripped, count=1).strip()
        if stripped == probe:
            break
        probe = stripped
    bracket = _LEADING_BRACKET_RE.match(probe)
    if bracket:
        return bracket.group(1).strip()
    return probe


def is_method_only_title(text: str) -> bool:
    """方法/科室标签本身(超声提示/CT提示/外科/检查结论/眼底右…)不是发现标题。"""
    probe = str(text or "").strip().rstrip("：:").strip()
    while probe:
        stripped = _CN_ENUMERATOR_RE.sub("", probe, count=1)
        stripped = _ARABIC_ENUMERATOR_RE.sub("", stripped, count=1).strip()
        if stripped == probe:
            break
        probe = stripped
    probe = probe.rstrip("：:").strip()
    return bool(_METHOD_ONLY_RE.fullmatch(probe))


def _is_useful_title(text: str, profile: SzbfProfile) -> bool:
    if not text or len(text) < 2:
        return False
    if not re.search(r"[A-Za-z\u4e00-\u9fa5]", text):
        return False
    probe = _title_probe(text)
    if not probe or _SECTION_LABEL_RE.match(probe):
        return False
    if is_method_only_title(text) or is_method_only_title(probe):
        return False
    if len(text) > 20 and _ADVICE_WORDS_RE.search(text):
        return False
    if any(p in text for p in profile.normal_phrases):
        return False
    if "阴性" in text and "阳性" not in text:
        return False
    if "建议" in text:
        return False
    if text.startswith(profile.suggestion_prefixes):
        return False
    if _MEASUREMENT_RE.search(text):
        return False
    if len(text) > 60 and re.search(r"(是|可|引起|导致|提示|由于|因此|建议)", text):
        return False
    return True


def _classify(text: str) -> str:
    if any(term in text for term in _INDICATOR_TERMS) and _ABNORMAL_TAIL_RE.search(text):
        return "indicator"
    return "finding"


def _indicator_base_name(text: str) -> str:
    return re.sub(
        r"(偏高|偏低|升高|降低|增高|不足|异常|阳性)$",
        "",
        normalize_item_name(text),
    )


def _is_complete_finding(text: str, profile: SzbfProfile) -> bool:
    cleaned = _strip_prefix(text, profile)
    if len(cleaned) < 3:
        return False
    return bool(_COMPLETE_FINDING_RE.search(cleaned))


def _split_compound(segment: str, profile: SzbfProfile) -> list[str]:
    pieces = [part.strip() for part in _COMPOUND_SPLIT_RE.split(segment) if part.strip()]
    if len(pieces) > 1 and all(_is_complete_finding(p, profile) for p in pieces):
        return pieces
    return [segment]


def _split_multi(text: str, profile: SzbfProfile) -> list[str]:
    parts = []
    for clause in _CLAUSE_SPLIT_RE.split(text):
        for piece in _ENUM_RE.split(clause):
            piece = piece.strip()
            if piece:
                parts.extend(_split_compound(piece, profile))
    return parts


def _is_lab_line(line: str) -> bool:
    return bool(
        _NUM_LAB_RE.search(line)
        or _QUAL_LAB_RE.search(line)
        or _BLOOD_PRESSURE_RE.search(line)
    )


def _candidates(text: str, profile: SzbfProfile):
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if any(skip in line for skip in profile.extra_skip):
            continue
        match = _NUMBERED_RE.match(line)
        if match and "numbered" in profile.title_patterns:
            for part in _split_multi(match.group(1), profile):
                if _is_lab_line(part):
                    continue
                yield part
        if "bracket" in profile.title_patterns:
            for bracket in _BRACKET_RE.findall(line):
                if not bracket.strip().isdigit():
                    yield bracket
        if (
            "legacy" in profile.title_patterns
            and not match
            and not _BRACKET_RE.search(line)
            and not _is_lab_line(line)
        ):
            for segment in re.split(r"[,\n]", line):
                if not re.search(r"[：:]", segment) or _is_lab_line(segment):
                    continue
                head = re.split(r"[：:]", segment, 1)[0].strip()
                if 2 <= len(head) <= 30:
                    yield head


def _split_abnormal_suffix(text: str) -> tuple[str, str | None]:
    text = (text or "").strip()
    match = _ABNORMAL_TAIL_RE.search(text)
    if not match or match.end() != len(text):
        return text, None
    return text[: match.start()].rstrip(), normalize_deviation(match.group(1))


def _parse_lab_lines(text: str) -> list[ParsedIndicator]:
    result = []
    text = text or ""
    for match in _NUM_LAB_RE.finditer(text):
        if re.match(r"\s*\+", text[match.end():]):
            continue
        name = normalize_item_name(match.group(1))
        if (
            not _is_quality_indicator_name(_indicator_base_name(name))
            or name in _LAB_NAME_SKIP
            or any(x in name for x in ("建议", "参考", "提示", "病史", "历史", "单位", "范围", "结果"))
        ):
            continue
        deviation = normalize_deviation(match.group(4)) or normalize_deviation(match.group(0))
        if deviation is None:
            continue
        unit, _ = _split_abnormal_suffix(match.group(3) or "")
        paren, paren_deviation = _split_abnormal_suffix(match.group(5) or "")
        if not unit and paren and not paren_deviation and not re.search(r"\d\s*[-~—－]\s*\d", paren):
            unit = paren
        result.append(
            ParsedIndicator(name, match.group(2), unit or None, deviation, match.group(0))
        )
    for match in _QUAL_LAB_RE.finditer(text):
        name = normalize_item_name(match.group(1))
        if (
            not _is_quality_indicator_name(_indicator_base_name(name))
            or name in _LAB_NAME_SKIP
            or any(x in name for x in ("建议", "参考", "提示", "病史", "历史", "单位", "范围", "结果"))
        ):
            continue
        if match.group(2) == "阴性":
            continue
        result.append(ParsedIndicator(name, match.group(2), None, "异常", match.group(0)))
    return result


def parse_con_suggest(text: str | None, profile: SzbfProfile) -> ParsedConclusion:
    result = ParsedConclusion()
    seen_titles = set()
    indicator_index = {}
    for candidate in _candidates(text or "", profile):
        title = _strip_prefix(candidate, profile)
        if _is_lab_line(candidate) or _is_lab_line(title):
            continue
        if not _is_useful_title(title, profile):
            continue
        title = re.sub(r"\s+", " ", title).strip()
        normalized = normalize_item_name(title)
        if not normalized or normalized in seen_titles:
            continue
        kind = _classify(title)
        key = _indicator_base_name(title) if kind == "indicator" else None
        if kind == "indicator" and (
            not _is_quality_indicator_title(title)
            or not _is_quality_indicator_title(key or "")
            or not _is_quality_indicator_name(title)
            or not _is_quality_indicator_name(key or "")
        ):
            continue
        seen_titles.add(normalized)
        result.titles.append(ParsedTitle(title, kind, candidate))
        if kind == "indicator":
            if key not in indicator_index:
                indicator_index[key] = len(result.indicators)
                result.indicators.append(
                    ParsedIndicator(key, None, None, normalize_deviation(title), title)
                )
    for parsed in _parse_lab_lines(text or ""):
        key = parsed.item_name
        idx = indicator_index.get(key)
        if idx is None:
            indicator_index[key] = len(result.indicators)
            result.indicators.append(parsed)
        elif result.indicators[idx].result_value is None:
            result.indicators[idx] = parsed
    return result
