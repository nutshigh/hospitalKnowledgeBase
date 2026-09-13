"""规则引擎纯函数:报告的红黄判定集合 → 病种命中列表。

匹配口径(三元组: 名称 + 级别 + 方向):
- 单指标: mapping.item_name_standard 与判定条目原始名或指标标准名相等,
  且 判定颜色 >= mapping.match_level, 且 deviation 归一后 == mapping.match_deviation;
- 组合: disease_rule.member_items 每个成员都在异常名集合中(严格 AND),
  每个成员的 min_level / deviation 均须满足;
- 同报告同病种合并为一行, combo 优先于 single, hit_items 合并;
- 同指标多映射取最严(B 决策): 同一判定条目命中的多个映射,
  仅保留级别要求最严的那组(如 BMI 红时只记肥胖症, 不记超重)。
"""

import re

_LEVEL_RANK = {"green": 0, "yellow": 1, "red": 2}

# deviation 双值域归一: rules_engine 产 high/low, LLM 产 偏高/偏低/异常
_DEV_MAP = {
    "high": "偏高", "偏低": "偏低", "low": "偏低", "偏高": "偏高",
    "偏大": "偏高", "偏小": "偏低", "偏重": "偏高", "偏轻": "偏低",
    "异常": "异常", "阳性": "阳性",
}


def normalize_deviation(raw):
    if not raw:
        return None
    return _DEV_MAP.get(str(raw), str(raw))


def _level_rank(level) -> int:
    return _LEVEL_RANK.get(str(level or "YELLOW").lower(), 1)


def _satisfies(judgment, min_level, deviation) -> bool:
    """判定条目是否满足 (min_level, deviation) 条件。映射要求方向时判定必须完全一致。"""
    lv = getattr(judgment, "color_level", "green")
    if _LEVEL_RANK.get(lv, 0) < _level_rank(min_level):
        return False
    if deviation:
        if normalize_deviation(getattr(judgment, "deviation", None)) != deviation:
            return False
    return True


def _build_name_map(judgments, indicator_std_names):
    """每个异常名字 → 判定条目列表(保留 raw/std 两键)。

    返回 (name_map, conclusions): conclusions 为结论型判定条目(source='conclusion'),
    供子串匹配兜底(见 compute_hits)。"""
    name_map = {}
    conclusions = []
    for j in judgments:
        if j.color_level not in ("red", "yellow"):
            continue
        for name in {j.item_name, indicator_std_names.get(getattr(j, "indicator_id", None))}:
            if name:
                name_map.setdefault(name, []).append(j)
        if getattr(j, "source", None) == "conclusion":
            conclusions.append(j)
    return name_map, conclusions


# 结论型子串匹配的最小标准名长度: 防"胆囊""尿糖"等 2 字短名误命中
# ("胆囊多发结节"含"胆囊"、"尿糖阳性"含"尿糖"), 短名只走精确匹配。
_MIN_SUBSTR_LEN = 3


def compute_hits(judgments, indicator_std_names, mappings, rules):
    """judgments: 判定条目对象(item_name/color_level/deviation/indicator_id/source)。
    indicator_std_names: {indicator_id: 标准名}。
    mappings: [{"id","item_name_standard","disease_name","disease_category",
                "disease_class","match_level","match_deviation"}]
    rules: [{"id","disease_name","disease_category","disease_class",
             "member_items": [{"name","min_level","deviation"}]}]
    返回 [{"disease_name","disease_category","disease_class","hit_type",
           "mapping_id","rule_id","hit_items"}] (病种合并, combo 优先, 取最严)。

    匹配口径(2026-08-19 起):
    - 生化/指标型: 精确匹配(归一化标准名), 避免"低密度脂蛋白"类子串误命中;
    - 结论型(source='conclusion'): 精确匹配 + 子串匹配兜底
      (标准名 ≥3 字, 双向包含), 覆盖"脂肪肝(中度)"类自由文本;
    - 组合: 成员严格 AND(仅精确匹配, 成员均为归一化标准名)。
    """
    name_map, conclusions = _build_name_map(judgments, indicator_std_names)

    # 阶段1: 单指标 —— 每判定条目取最严映射组(同条目命中多个映射时按 rank 保留最高组)
    judge_hits = {}  # id(judgment) -> [(rank, mapping)]
    for m in mappings:
        std = m["item_name_standard"]
        judges = name_map.get(std)
        if not judges and conclusions and len(std) >= _MIN_SUBSTR_LEN:
            # 结论型子串兜底: 标准名与结论名互相包含(如 "脂肪肝" in "脂肪肝(中度)")
            judges = [j for j in conclusions if std in j.item_name or j.item_name in std]
            if not judges:
                # 2026-08-27: 去括号后再试(结论原文"右肺尖间隔旁型肺气肿" vs
                # 标准名"肺气肿(间隔旁型)") — 括号修饰不影响语义包含
                std_clean = re.sub(r"[（(][^）)]*[）)]", "", std)
                if std_clean and std_clean != std:
                    judges = [j for j in conclusions
                              if std_clean in j.item_name or j.item_name in std_clean]
        if not judges:
            continue
        rank = _level_rank(m.get("match_level"))
        for j in judges:
            if _satisfies(j, m.get("match_level"), m.get("match_deviation")):
                judge_hits.setdefault(id(j), []).append((rank, m))

    single_pool = []  # (rank, mapping)
    for jid, ms in judge_hits.items():
        top = max(r for r, _ in ms)
        single_pool.extend((r, m) for r, m in ms if r == top)

    # 阶段2: 组合 —— 成员条件须满足
    combo_pool = []
    for r in rules:
        matched = []
        ok = True
        for mem in r["member_items"]:
            judges = name_map.get(mem["name"])
            if not judges or not any(_satisfies(j, mem.get("min_level"), mem.get("deviation")) for j in judges):
                ok = False
                break
            matched.append(mem["name"])
        if ok:
            combo_pool.append((r, matched))

    # 阶段3: 合并 —— 同病种 combo 优先于 single, hit_items 并集
    hits = {}

    def merge(hit_type, mapping_id, rule_id, name, category, klass, items):
        cur = hits.get(name)
        if cur is None:
            hits[name] = {
                "disease_name": name, "disease_category": category,
                "disease_class": klass, "hit_type": hit_type,
                "mapping_id": mapping_id, "rule_id": rule_id,
                "hit_items": sorted(set(items)),
            }
            return
        if hit_type == "combo" and cur["hit_type"] != "combo":
            cur["hit_type"] = "combo"
            cur["mapping_id"] = None
            cur["rule_id"] = rule_id
        elif hit_type == "single" and cur["hit_type"] == "single":
            cur["mapping_id"] = mapping_id
        cur["hit_items"] = sorted(set(cur["hit_items"]) | set(items))

    for rank, m in single_pool:
        merge("single", m["id"], None, m["disease_name"],
              m["disease_category"], m.get("disease_class"), [m["item_name_standard"]])
    for r, matched in combo_pool:
        merge("combo", None, r["id"], r["disease_name"],
              r["disease_category"], r.get("disease_class"), matched)

    return list(hits.values())
