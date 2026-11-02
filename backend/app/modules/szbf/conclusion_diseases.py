from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ConclusionDisease:
    name: str
    category: str = "OTHER"
    klass: str = "其他"


_DEFS = (
    ConclusionDisease("脂肪肝", "CHRONIC", "消化系统"),
    ConclusionDisease("肝囊肿", "OTHER", "消化系统"),
    ConclusionDisease("肝内钙化灶", "OTHER", "消化系统"),
    ConclusionDisease("胆囊息肉样病变", "CHRONIC", "消化系统"),
    ConclusionDisease("肾囊肿", "OTHER", "泌尿系统"),
    ConclusionDisease("前列腺钙化灶", "OTHER", "泌尿系统"),
    ConclusionDisease("前列腺增生", "CHRONIC", "泌尿系统"),
    ConclusionDisease("甲状腺结节", "OTHER", "其他"),
    ConclusionDisease("肺结节", "OTHER", "其他"),
    ConclusionDisease("甲状腺弥漫性病变", "CHRONIC", "内分泌代谢"),
    ConclusionDisease("颈动脉内中膜增厚", "CHRONIC", "心血管系统"),
    ConclusionDisease("颈动脉粥样硬化", "CHRONIC", "心血管系统"),
    ConclusionDisease("瓣膜反流", "OTHER", "心血管系统"),
    ConclusionDisease("高血压", "CHRONIC", "心血管系统"),
    ConclusionDisease("心律失常", "CHRONIC", "心血管系统"),
    ConclusionDisease("窦性心动过缓", "CHRONIC", "心血管系统"),
    ConclusionDisease("窦性心律不齐", "CHRONIC", "心血管系统"),
    ConclusionDisease("ST-T改变", "CHRONIC", "心血管系统"),
    ConclusionDisease("血脂异常", "CHRONIC", "心血管系统"),
    ConclusionDisease("高尿酸血症", "CHRONIC", "内分泌代谢"),
    ConclusionDisease("糖尿病", "CHRONIC", "内分泌代谢"),
    ConclusionDisease("超重", "CHRONIC", "内分泌代谢"),
    ConclusionDisease("维生素D不足", "OTHER", "内分泌代谢"),
    ConclusionDisease("骨量减少", "OTHER", "其他"),
    ConclusionDisease("幽门螺杆菌阳性", "CHRONIC", "消化系统"),
    ConclusionDisease("牙周病", "CHRONIC", "其他"),
    ConclusionDisease("龋齿", "OTHER", "其他"),
    ConclusionDisease("鼻炎", "OTHER", "其他"),
    ConclusionDisease("咽炎", "OTHER", "其他"),
    ConclusionDisease("鼻中隔偏曲", "OTHER", "其他"),
    ConclusionDisease("痔疮", "OTHER", "其他"),
    ConclusionDisease("视力减退", "OTHER", "其他"),
    ConclusionDisease("屈光不正", "OTHER", "其他"),
    ConclusionDisease("子宫肌瘤", "OTHER", "其他"),
    ConclusionDisease("乳腺增生", "OTHER", "其他"),
    ConclusionDisease("乳腺结节", "OTHER", "其他"),
    ConclusionDisease("盆腔积液", "OTHER", "其他"),
)

_ALIASES = {
    "脂肪肝": ("脂肪肝",),
    "肝囊肿": ("肝囊肿", "肝多发囊肿"),
    "肝内钙化灶": ("肝内钙化灶",),
    "胆囊息肉样病变": ("胆囊息肉", "胆囊多发息肉", "胆囊息肉样病变"),
    "肾囊肿": ("肾囊肿", "双肾囊肿"),
    "前列腺钙化灶": ("前列腺钙化灶", "前列腺钙化"),
    "前列腺增生": ("前列腺增生",),
    "甲状腺结节": ("甲状腺结节", "甲状腺右叶结节", "甲状腺左叶结节",
                     "甲状腺双侧叶结节", "甲状腺双侧叶多发结节",
                     "甲状腺多发结节"),
    "肺结节": ("肺结节",),
    "甲状腺弥漫性病变": ("甲状腺弥漫性病变", "桥本氏甲状腺炎", "桥本甲状腺炎"),
    "颈动脉内中膜增厚": ("颈动脉内中膜增厚",),
    "颈动脉粥样硬化": ("颈动脉粥样硬化", "颈动脉斑块", "粥样硬化斑块"),
    "瓣膜反流": ("二尖瓣反流", "三尖瓣反流", "主动脉瓣反流", "瓣膜反流"),
    "高血压": ("高血压",),
    "心律失常": ("心律失常",),
    "窦性心动过缓": ("窦性心动过缓",),
    "窦性心律不齐": ("窦性心律不齐",),
    "ST-T改变": ("ST-T改变", "ST-T 改变", "T波异常"),
    "血脂异常": ("血脂异常", "高脂血症"),
    "高尿酸血症": ("高尿酸血症", "尿酸偏高", "尿酸升高", "尿酸增高"),
    "糖尿病": ("糖尿病", "空腹血糖升高", "血糖偏高"),
    "超重": ("超重", "肥胖"),
    "维生素D不足": ("维生素D不足", "25羟基维生素D不足", "25-羟维生素D不足"),
    "骨量减少": ("骨量减少", "骨密度降低"),
    "幽门螺杆菌阳性": ("幽门螺杆菌阳性", "Hp检验阳性", "胃幽门螺杆菌阳性"),
    "牙周病": ("牙周病", "牙周炎", "牙结石", "牙龈炎"),
    "龋齿": ("龋齿",),
    "鼻炎": ("鼻炎",),
    "咽炎": ("咽炎",),
    "鼻中隔偏曲": ("鼻中隔偏曲", "鼻中隔弯曲"),
    "痔疮": ("痔疮",),
    "视力减退": ("视力减退", "视力下降"),
    "屈光不正": ("屈光不正", "近视", "远视", "散光"),
    "子宫肌瘤": ("子宫肌瘤",),
    "乳腺增生": ("乳腺增生",),
    "乳腺结节": ("乳腺结节", "乳腺实性结节"),
    "盆腔积液": ("盆腔积液",),
}

_NEGATION_RE = re.compile(r"(未见|未见明显|无|排除|除外|待排|不排除|否认)")
_SUGGESTION_RE = re.compile(r"(建议|请|必要时|复查|随诊|就诊|治疗|预防|注意)")


def is_excluded_finding_context(text: str) -> bool:
    """否定/建议语境不是阳性发现, 不参与词典匹配与 OTHER 自映射。"""
    value = str(text or "")
    return bool(_NEGATION_RE.search(value) or _SUGGESTION_RE.search(value))


def match_conclusion_diseases(title: str) -> list[ConclusionDisease]:
    text = str(title or "").strip()
    if not text or _NEGATION_RE.search(text) or _SUGGESTION_RE.search(text):
        return []
    hits = []
    seen = set()
    for disease in _DEFS:
        if disease.name == "肺结节":
            # "双肺多发结节"/"左肺下叶微小结节" 等不定别名, 按词素命中。
            if "肺" in text and "结节" in text:
                seen.add(disease.name)
                hits.append(disease)
            continue
        for alias in _ALIASES.get(disease.name, (disease.name,)):
            if alias and alias in text:
                if disease.name not in seen:
                    seen.add(disease.name)
                    hits.append(disease)
                break
    return hits
