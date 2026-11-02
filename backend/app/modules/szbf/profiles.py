from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SzbfProfile:
    org_id: str
    keywords: tuple[str, ...] = ()
    title_patterns: tuple[str, ...] = ("numbered", "bracket", "legacy")
    normal_phrases: tuple[str, ...] = (
        "未见明显异常", "未见异常", "大致正常", "未见明显占位",
        "未见明显肿物", "未见明显增生", "正常范围", "未见明显",
    )
    suggestion_prefixes: tuple[str, ...] = (
        "建议", "若", "如", "请", "必要时", "定期", "注意", "可",
        "应", "需", "进一步", "复查", "治疗", "饮食", "运动",
        "戒烟", "限酒", "避免", "控制", "监测", "随诊",
    )
    method_prefixes: tuple[str, ...] = (
        "心电图", "心脏彩超", "超声", "B超", "彩超", "CT", "胸片",
        "X线", "查体", "内科", "外科", "眼科", "耳鼻喉", "妇科",
        "肛诊", "检验", "呼气试验", "C13", "C14", "骨密度",
        "口腔", "视功能", "血压", "体重指数", "腰臀比",
    )
    extra_skip: tuple[str, ...] = ()
    extra_include: tuple[str, ...] = ()


DEFAULT_PROFILE = SzbfProfile(org_id="default")

PROFILES = {
    "133": SzbfProfile(
        org_id="133",
        keywords=("北京总站",),
    ),
}


def get_profile(org_id: str, org_name: str = "") -> SzbfProfile:
    profile = PROFILES.get(str(org_id))
    if profile:
        return profile
    if org_name:
        for candidate in PROFILES.values():
            if any(k in org_name for k in candidate.keywords):
                return candidate
    return DEFAULT_PROFILE
