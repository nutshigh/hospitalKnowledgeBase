"""验收演示: 用两份真实 PDF 的异常指标构造判定集, 跑风险引擎展示命中效果。

判定集模拟"解析+红黄判定"后的 indicator_judgment 数据(名字用归一化标准名)。
"""
import json

from app.modules.risk.engine import compute_hits
from app.modules.risk.seed import CENTRAL_MAPPINGS, CENTRAL_RULES


def J(name, level, deviation=None):
    class _J:
        def __init__(self):
            self.item_name = name
            self.color_level = level
            self.deviation = deviation
            self.indicator_id = None
    return _J()


def demo():
    mappings = [
        {"id": i, "item_name_standard": m[0], "disease_name": m[1],
         "disease_category": m[2], "disease_class": m[3],
         "match_level": m[4], "match_deviation": m[5]}
        for i, m in enumerate(CENTRAL_MAPPINGS)
    ]
    rules = [
        {"id": i, "rule_code": r[0], "disease_name": r[1],
         "disease_category": r[2], "disease_class": r[3], "member_items": r[4]}
        for i, r in enumerate(CENTRAL_RULES)
    ]

    step = {"name": "步新宇(北京医院, 23岁男)", "judgments": [
        J("肌酸激酶（CK）", "yellow", "偏高"),          # 242↑
        J("血清同型半胱氨酸", "yellow", "偏高"),        # 16.9↑
        J("游离前列腺特异性抗原（FPSA）", "yellow", "偏高"),  # 1.184↑
        J("舒张压（DBP）", "yellow", "偏低"),           # 58↓ 方向验证
        J("室上性早搏", "yellow", None),                # 心电图
        J("肺气肿（间隔旁型）", "yellow", None),        # CT
        J("甲状腺囊性结节", "yellow", None),            # B超 TI-RADS 2级
        J("肝内钙化灶", "yellow", None),                # 腹部B超
        J("外耳道耵聍", "yellow", None),
        J("龋齿", "yellow", None),
    ]}
    mei = {"name": "陈美杉(友谊医院, 31岁女)", "judgments": [
        J("体质指数（BMI）", "yellow", "偏高"),          # 26.38 超重(黄) → 不应命中肥胖症
        J("肺结节", "yellow", None),                    # CT 微小结节 → 肺癌(疑似)
        J("胆囊多发结节", "yellow", None),
        J("胆囊壁胆固醇结晶", "yellow", None),
        J("窦性心律不齐", "yellow", None),
        J("屈光不正", "yellow", None),
        J("扁桃体肥大", "yellow", None),
        J("牙龈炎", "yellow", None),
        J("牙结石", "yellow", None),
        J("血小板压积（PCT）", "yellow", "偏高"),        # 0.33↑ 无映射 → 不命中
    ]}

    for case in [step, mei]:
        print("=" * 60)
        print(case["name"])
        hits = compute_hits(case["judgments"], {}, mappings, rules)
        if not hits:
            print("  (无命中)")
        for h in sorted(hits, key=lambda x: (x["disease_category"], x["disease_name"])):
            mark = {"CHRONIC": "慢性病", "MAJOR": "重大疾病", "OTHER": "危险因素"}.get(h["disease_category"], h["disease_category"])
            print(f"  [{mark}] {h['disease_name']} ({h['hit_type']}) 命中指标: {h['hit_items']}")

    # 组合规则演示: 收缩压+舒张压同高 → C-HT 高血压
    print("=" * 60)
    print("组合规则演示: 收缩压+舒张压同高")
    hits = compute_hits([
        J("收缩压（SBP）", "yellow", "偏高"),
        J("舒张压（DBP）", "yellow", "偏高"),
    ], {}, mappings, rules)
    print(" ", [(h["disease_name"], h["hit_type"], h["hit_items"]) for h in hits])

    # 取最严演示: BMI 红 → 只记肥胖症
    print("=" * 60)
    print("取最严演示: BMI 红色判定")
    hits = compute_hits([J("体质指数（BMI）", "red", "偏高")], {}, mappings, rules)
    print(" ", [(h["disease_name"], h["hit_type"]) for h in hits])


if __name__ == "__main__":
    demo()
