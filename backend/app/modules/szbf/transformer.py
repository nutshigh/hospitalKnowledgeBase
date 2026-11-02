from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.core.term_normalizer import normalize_item_name as canonical_item_name
from app.modules.szbf.conclusion_diseases import (
    ConclusionDisease,
    is_excluded_finding_context,
    match_conclusion_diseases,
)
from app.modules.szbf.conclusion_parser import (
    _INDICATOR_TERMS,
    is_method_only_title,
    parse_con_suggest,
)
from app.modules.szbf.normalize import (
    compute_age,
    is_imaging_item,
    normalize_deviation,
    normalize_gender,
    normalize_item_name,
    parse_date,
    parse_reference_range,
    synthetic_anchor,
)
from app.modules.szbf.profiles import SzbfProfile


@dataclass
class SourceExam:
    user_id: str
    name: str | None
    gender: str | None
    birthday: date | None
    org_id: str
    org_name: str
    exam_code: str
    exam_date: date | None
    details: list[dict]
    con_suggest: str | None
    conclusion: str | None


@dataclass
class TargetIndicator:
    item_name: str
    item_name_standard: str | None
    item_code: str | None
    result_value: str | None
    unit: str | None
    ref_range_low: str | None
    ref_range_high: str | None
    category: str | None
    raw_text: str | None
    source: str
    deviation: str | None


@dataclass
class TargetReport:
    source_user_id: str
    exam_code: str
    user_id: str
    name: str | None
    gender: str | None
    age: int | None
    report_date: date | None
    unit_name: str
    conclusion_text: str | None
    raw_conclusion: str | None
    raw_con_suggest: str | None
    indicators: list[TargetIndicator] = field(default_factory=list)
    conclusion_diseases: list[ConclusionDisease] = field(default_factory=list)


def _float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


_CANONICAL_ALIASES = {
    # 25-OH vitamin D
    "25-羟维生素D": "25羟基维生素D",
    "25-羟基维生素D": "25羟基维生素D",
    "25-羟基维生素D降低": "25羟基维生素D",
    "羟基维生素D": "25羟基维生素D",
    "维生素D": "25羟基维生素D",
    # lipoprotein(a)
    "血清脂蛋白(a)": "脂蛋白(a)",
    "血清脂蛋白Lp(a)": "脂蛋白(a)",
    "脂蛋白Lp(a)": "脂蛋白(a)",
    # gastrin-17
    "胃泌素-17检测": "胃泌素-17",
    "胃泌素17(免疫学方法)": "胃泌素-17",
    # blood counts
    "血小板": "血小板数(PLT)",
    "血小板总数": "血小板数(PLT)",
    "淋巴细胞总数": "淋巴细胞绝对值(LYM#)",
    "白细胞总数": "白细胞数(WBC)",
    "中性粒细胞总数": "中性粒细胞绝对值(NEUT#)",
    "单核细胞总数": "单核细胞绝对值(MONO#)",
    "嗜碱性粒细胞总数": "嗜碱性粒细胞绝对值",
    "嗜碱性粒细胞绝对值": "嗜碱性粒细胞绝对值",
    "嗜碱性细胞比例(BASO)": "嗜碱性粒细胞百分数",
    "嗜碱细胞绝对值(BASO#)": "嗜碱性粒细胞绝对值",
    "嗜碱性粒细胞百分数": "嗜碱性粒细胞百分数",
    # liver enzyme
    "天冬氨酸氨基转移酶": "天门冬氨酸氨基转移酶(谷草酶)",
    "天冬氨酸氨基转移酶(谷草酶)": "天门冬氨酸氨基转移酶(谷草酶)",
}


def _canonical(name):
    normalized = normalize_item_name(name)
    alias = _CANONICAL_ALIASES.get(normalized)
    if alias:
        return alias
    try:
        std, _ = canonical_item_name(normalized)
    except Exception:
        return normalized or name
    std = std or normalized
    return _CANONICAL_ALIASES.get(std, std)


def _indicator_from_detail(item: dict) -> TargetIndicator:
    name = normalize_item_name(item.get("item_name"))
    low, high = parse_reference_range(item.get("reference_range"))
    return TargetIndicator(
        item_name=name,
        item_name_standard=_canonical(name),
        item_code=item.get("item_code"),
        result_value=item.get("result"),
        unit=item.get("item_unit"),
        ref_range_low=low,
        ref_range_high=high,
        category=item.get("dept_name"),
        raw_text=item.get("item_name"),
        source="indicator",
        deviation=normalize_deviation(item.get("exception_tips")),
    )


_SELF_MAP_MAX_LEN = 30
_SELF_MAP_PUNCT_RE = re.compile(r"[，,；;。]")
_SELF_MAP_METHOD_RE = re.compile(r"(测量方法|检查方法|检测方法)")
_SELF_MAP_METHOD_WORD = (
    r"(?:"
    r"胸部CT|头颅CT|腹部CT|放射科(?:\(CT\)|（CT）)?|"
    r"超声心动图|超声骨密度检测|超声骨密度|"
    r"心脏彩超|腹部彩超|颈动脉彩超|甲状腺彩超|乳腺彩超|"
    r"妇科超声|妇科彩超|妇科TCT|眼科眼底|"
    r"超声|B超|彩超|CT|核磁|MRI|X线|胸片|动态心电图|心电图|"
    r"骨密度|查体|内科|外科|眼科|口腔科|耳鼻喉科|妇科|检验科|体检|影像学"
    r")"
)
_METHOD_PREFIX_RE = re.compile(
    r"^\s*(?:\d+\s*[.、．]\s*)?(?:一般)?"
    + _SELF_MAP_METHOD_WORD
    + r"(?:\s*[及和、]\s*" + _SELF_MAP_METHOD_WORD + r")*"
    r"(?:检查|查体|检验|检测|测定|结果|提示|结论|报告|室|科)*"
    r"\s*(?:[（(][^）)]{1,8}[）)])?\s*[：:]?\s*"
)
_SELF_MAP_MEASUREMENT_RE = re.compile(
    r"^(?:"
    r"(?:本次体检|体检)?(?:血压|收缩压|舒张压|脉搏)"
    r"|(?:左|右|双)?眼(?:球)?眼压"
    r"|偏瘦|超重|肥胖|体重指数|体质指数|BMI|腰围|臀围|身高|体重|腰臀比"
    r")",
    re.I,
)
_SELF_MAP_NORMAL_RE = re.compile(
    r"^(?:未见?明显?(?:异常|病变|异常回声)|正常|大致正常|无异常|正常范围)$"
)
_SELF_MAP_MEANINGFUL_RE = re.compile(r"[A-Za-z\u4e00-\u9fa5]")
_SELF_MAP_INDICATOR_TAIL_RE = re.compile(
    r"(?:偏高|偏低|升高|降低|增高|不足|异常|阳性)$"
)
_SELF_MAP_LATIN_ONLY_RE = re.compile(r"^[A-Za-z0-9#%+\-/().\s]+$")
_SELF_MAP_INDICATOR_NAMES = frozenset(_INDICATOR_TERMS) | frozenset({
    "尿白细胞", "尿红细胞", "尿亚硝酸盐", "尿胆红素", "维生素D",
})


def _clean_finding_title(title: str) -> str:
    """剥离方法/科室前缀(其后带内容), 如 放射科提示：胸椎侧弯 -> 胸椎侧弯。"""
    text = str(title or "").strip()
    for _ in range(2):
        cleaned = _METHOD_PREFIX_RE.sub("", text).strip()
        if cleaned == text:
            break
        text = cleaned
    return text


def is_self_map_candidate(title: str) -> bool:
    """自映射到 OTHER 的病名候选: 排除测量项/指标/方法/拉丁缩写/叙述句。"""
    text = _clean_finding_title(title)
    if not text or len(text) < 2 or len(text) > _SELF_MAP_MAX_LEN:
        return False
    if len(_SELF_MAP_MEANINGFUL_RE.findall(text)) < 2:
        return False
    if _SELF_MAP_NORMAL_RE.fullmatch(text):
        return False
    if _SELF_MAP_PUNCT_RE.search(text):
        return False
    if _SELF_MAP_METHOD_RE.search(text):
        return False
    if _SELF_MAP_MEASUREMENT_RE.match(text):
        return False
    if _SELF_MAP_INDICATOR_TAIL_RE.search(text):
        return False
    if _SELF_MAP_LATIN_ONLY_RE.fullmatch(text):
        return False
    if text in _SELF_MAP_INDICATOR_NAMES:
        return False
    if is_excluded_finding_context(text):
        return False
    return not is_method_only_title(text)


def transform_exam(exam: SourceExam, profile: SzbfProfile,
                   indicator_source: str = "merge") -> TargetReport:
    details = [
        d for d in exam.details
        if str(d.get("is_combine")) == "0" and str(d.get("delete_flag", "0")) == "0"
    ]
    detail_names: set[str] = set()
    for item in details:
        key = _canonical(normalize_item_name(item.get("item_name")))
        if key:
            detail_names.add(key)
    detail_indicators: list[TargetIndicator] = []
    imaging_findings: list[str] = []
    for item in details:
        if str(item.get("exception_flag")) != "1":
            continue
        name = item.get("item_name")
        if is_imaging_item(name):
            result = str(item.get("result") or "").strip()
            if result and "未见明显异常" not in result:
                imaging_findings.append(result)
            continue
        detail_indicators.append(_indicator_from_detail(item))

    parsed = parse_con_suggest(exam.con_suggest, profile)
    parsed_indicators = [
        TargetIndicator(
            item_name=i.item_name,
            item_name_standard=_canonical(i.item_name),
            item_code=None,
            result_value=i.result_value,
            unit=i.unit,
            ref_range_low=None,
            ref_range_high=None,
            category="结论段",
            raw_text=i.raw,
            source="indicator",
            deviation=i.deviation,
        )
        for i in parsed.indicators
    ]

    if indicator_source == "detail":
        indicators = detail_indicators
    elif indicator_source == "con_suggest":
        indicators = parsed_indicators
    else:
        indicators = list(detail_indicators)
        existing = set(detail_names)
        for item in parsed_indicators:
            key = _canonical(item.item_name_standard or item.item_name)
            if key and key not in existing:
                existing.add(key)
                indicators.append(item)

    findings = [t.name for t in parsed.titles if t.kind == "finding"]
    findings.extend(imaging_findings)
    seen_findings = set()
    for title in findings:
        key = normalize_item_name(title)
        if key in seen_findings:
            continue
        seen_findings.add(key)
        indicators.append(TargetIndicator(
            item_name=title,
            item_name_standard=None,
            item_code=None,
            result_value=None,
            unit=None,
            ref_range_low=None,
            ref_range_high=None,
            category="结论段",
            raw_text=title,
            source="conclusion",
            deviation=None,
        ))
    diseases: list[ConclusionDisease] = []
    seen = set()
    for title in findings:
        matched = match_conclusion_diseases(title)
        if matched:
            for disease in matched:
                if disease.name not in seen:
                    seen.add(disease.name)
                    diseases.append(disease)
            continue
        # spec §6.4: 词典未命中的发现按 OTHER 自映射(病名=清洗后的标题), 避免统计落"其他"桶。
        if not is_self_map_candidate(title):
            continue
        cleaned_title = _clean_finding_title(title)
        key = normalize_item_name(cleaned_title)
        if key and key not in seen:
            seen.add(key)
            diseases.append(ConclusionDisease(cleaned_title, "OTHER", "其他"))

    for item in details:
        if normalize_item_name(item.get("item_name")) in ("体重指数", "BMI", "体质指数"):
            value = _float(item.get("result"))
            if str(item.get("exception_flag")) == "1" and value is not None and value >= 24:
                if "超重" not in seen:
                    seen.add("超重")
                    diseases.append(ConclusionDisease("超重", "CHRONIC", "内分泌代谢"))

    return TargetReport(
        source_user_id=str(exam.user_id),
        exam_code=str(exam.exam_code),
        user_id=synthetic_anchor(exam.user_id),
        name=exam.name,
        gender=normalize_gender(exam.gender),
        age=compute_age(exam.birthday, exam.exam_date),
        report_date=exam.exam_date,
        unit_name=exam.org_name,
        conclusion_text=exam.con_suggest,
        raw_conclusion=exam.conclusion,
        raw_con_suggest=exam.con_suggest,
        indicators=indicators,
        conclusion_diseases=diseases,
    )
