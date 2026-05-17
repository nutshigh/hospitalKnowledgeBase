from typing import Optional, Dict

_STANDARD_MAP: Dict[str, str] = {
    "血糖": "空腹血糖（GLU）",
    "葡萄糖": "空腹血糖（GLU）",
    "糖化血红蛋白": "糖化血红蛋白（HbA1c）",
    "总胆固醇": "总胆固醇（TC）",
    "甘油三酯": "甘油三酯（TG）",
    "高密度脂蛋白": "高密度脂蛋白胆固醇（HDL-C）",
    "低密度脂蛋白": "低密度脂蛋白胆固醇（LDL-C）",
    "谷丙转氨酶": "丙氨酸氨基转移酶（ALT）",
    "谷草转氨酶": "天门冬氨酸氨基转移酶（AST）",
    "尿酸": "尿酸（UA）",
    "肌酐": "肌酐（Cr）",
    "尿素氮": "尿素氮（BUN）",
    "白细胞": "白细胞计数（WBC）",
    "红细胞": "红细胞计数（RBC）",
    "血红蛋白": "血红蛋白（Hb）",
    "血小板": "血小板计数（PLT）",
}


def normalize_item_name(raw_name: str) -> tuple:
    cleaned = raw_name.strip().replace(" ", "").replace("　", "")
    for alias, standard in _STANDARD_MAP.items():
        if alias in cleaned:
            return standard, None
    return raw_name.strip(), None


def normalize_indicators(indicators: list[dict]) -> list[dict]:
    for ind in indicators:
        name, code = normalize_item_name(ind.get("item_name", ""))
        ind["item_name_standard"] = name
        ind["item_code"] = code
    return indicators
