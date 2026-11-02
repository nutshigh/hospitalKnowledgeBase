from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

_GENDER = {"1": "男", "2": "女", "男": "男", "女": "女"}
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S")
_GENDER_SUFFIX_RE = re.compile(r"(?<=[)）\]】])[男女]$")
_KNOWN_GENDER_NAMES = (
    "红细胞数", "血红蛋白", "丙氨酸氨基转移酶",
    "天门冬氨酸氨基转移酶", "谷氨酰转移酶",
)
_SPACE_RE = re.compile(r"\s+")
_RANGE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*[-~—－]\s*(-?\d+(?:\.\d+)?)")
_UPPER_RE = re.compile(r"^\s*[<≤]\s*(\d+(?:\.\d+)?)")
_LOWER_RE = re.compile(r"^\s*[>≥]\s*(\d+(?:\.\d+)?)")
_DEVIATION = {
    "↑": "偏高", "偏高": "偏高", "升高": "偏高", "增高": "偏高",
    "↓": "偏低", "偏低": "偏低", "降低": "偏低", "减低": "偏低",
    "异常": "异常", "阳性": "异常",
}
_IMAGING_ITEMS = {"心脏", "肺部", "腹部", "其它眼疾", "其它"}


def normalize_gender(raw) -> str | None:
    if raw is None:
        return None
    return _GENDER.get(str(raw).strip())


def parse_date(raw) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def compute_age(birthday: date | None, exam_date: date | None) -> int | None:
    if not birthday or not exam_date:
        return None
    age = exam_date.year - birthday.year
    if (exam_date.month, exam_date.day) < (birthday.month, birthday.day):
        age -= 1
    return age if 0 <= age <= 120 else None


def synthetic_anchor(source_user_id) -> str:
    digest = hashlib.sha1(str(source_user_id).encode("utf-8")).hexdigest()
    return "z" + digest[:15]


def normalize_item_name(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = text.replace("（", "(").replace("）", ")")
    text = _SPACE_RE.sub("", text)
    if _GENDER_SUFFIX_RE.search(text):
        text = text[:-1]
    elif text.endswith(("男", "女")) and text.startswith(_KNOWN_GENDER_NAMES):
        text = text[:-1]
    return text


def parse_reference_range(raw) -> tuple[str | None, str | None]:
    text = str(raw or "").strip().replace("～", "-")
    match = _RANGE_RE.search(text)
    if match:
        return match.group(1), match.group(2)
    match = _UPPER_RE.search(text)
    if match:
        return None, match.group(1)
    match = _LOWER_RE.search(text)
    if match:
        return match.group(1), None
    return None, None


def normalize_deviation(raw) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    for key, value in _DEVIATION.items():
        if key in text:
            return value
    return None


def is_imaging_item(name) -> bool:
    return str(name or "").strip() in _IMAGING_ITEMS
