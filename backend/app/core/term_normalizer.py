import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

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
    """名称标准化 + 去重。

    体检 PDF 通常在多个章节（主检报告 / 医学科普 / 分项报告）逐一列出同一指标的同一
    数值；LLM 抽取时按章节各返回一条，DB 入库后会出现同名同值的多行。run_rules →
    filter_abnormal 会忠实于 DB 行数，导致 agent_search_knowledge 收到重复指标名、
    发重复 search_knowledge 调用、judge 也对重复指标重复审核。在此按
    (item_name_standard 或 item_name, result) 去重，保留首次出现，顺序不变。
    """
    for ind in indicators:
        name, code = normalize_item_name(ind.get("item_name", ""))
        ind["item_name_standard"] = name
        ind["item_code"] = code

    seen: set = set()
    deduped: list[dict] = []
    for ind in indicators:
        key = (
            ind.get("item_name_standard") or ind.get("item_name", ""),
            str(ind.get("result", "") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ind)

    if len(deduped) != len(indicators):
        logger.info(
            "normalize_indicators deduped %d -> %d (dropped %d duplicates)",
            len(indicators), len(deduped), len(indicators) - len(deduped),
        )
    return deduped