import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanonTerm:
    """词条。standard 为标准名;primary=False 表示子项/衍生物(指标走势隐藏)。"""
    standard: str
    primary: bool = True


# ---- 匹配辅助 ------------------------------------------------------------

# 清洗:去普通/全角空格
_SPACE = re.compile(r"[\s\u3000]+")

# 尾缀英文码括号 (…) /（…）。内容须为英文/数字/常用符号;中文限定语括号(镜检/尿/粪)不剥。
_CODE_PAREN = re.compile(
    r"[（(]\s*[A-Za-z0-9][A-Za-z0-9%.\-+#/:]*\s*[)）]\s*$"
)


def _clean(name: str) -> str:
    return _SPACE.sub("", name or "")


def _base(name: str) -> str:
    cleaned = _clean(name)
    if _CODE_PAREN.search(cleaned):
        return _CODE_PAREN.sub("", cleaned)
    return cleaned


# ---- 别名表:整名(清洗后)→ 词条 ------------------------------------------
# 匹配顺序:先整名(含括号)精确命中;未命中再剥尾缀英文码括号后的 base 精确命中。
# 未命中一律原名透传 —— 结构性杜绝「短别名吞长词」。

_ALIASES: Dict[str, CanonTerm] = {
    # ==== 主项(与历史 _STANDARD_MAP 的 canonical 字符串逐字一致) ====
    # 空腹血糖
    "血糖": CanonTerm("空腹血糖（GLU）"),
    "葡萄糖": CanonTerm("空腹血糖（GLU）"),
    "空腹血糖": CanonTerm("空腹血糖（GLU）"),
    # 糖化血红蛋白(含"全血糖化血红蛋白测定"这类含血糖词干但实为糖化的项)
    "糖化血红蛋白": CanonTerm("糖化血红蛋白（HbA1c）"),
    "全血糖化血红蛋白测定": CanonTerm("糖化血红蛋白（HbA1c）"),
    # 血脂
    "总胆固醇": CanonTerm("总胆固醇（TC）"),
    "甘油三酯": CanonTerm("甘油三酯（TG）"),
    "高密度脂蛋白": CanonTerm("高密度脂蛋白胆固醇（HDL-C）"),
    "高密度脂蛋白胆固醇": CanonTerm("高密度脂蛋白胆固醇（HDL-C）"),
    "低密度脂蛋白": CanonTerm("低密度脂蛋白胆固醇（LDL-C）"),
    "低密度脂蛋白胆固醇": CanonTerm("低密度脂蛋白胆固醇（LDL-C）"),
    # 肝功
    "谷丙转氨酶": CanonTerm("丙氨酸氨基转移酶（ALT）"),
    "谷草转氨酶": CanonTerm("天门冬氨酸氨基转移酶（AST）"),
    # 肾功
    "尿酸": CanonTerm("尿酸（UA）"),
    "肌酐": CanonTerm("肌酐（Cr）"),
    "尿素氮": CanonTerm("尿素氮（BUN）"),
    # 血常规主项
    "白细胞": CanonTerm("白细胞计数（WBC）"),
    "白细胞计数": CanonTerm("白细胞计数（WBC）"),
    "红细胞": CanonTerm("红细胞计数（RBC）"),
    "红细胞计数": CanonTerm("红细胞计数（RBC）"),
    "血红蛋白": CanonTerm("血红蛋白（Hb）"),
    "血小板": CanonTerm("血小板计数（PLT）"),
    "血小板计数": CanonTerm("血小板计数（PLT）"),

    # ==== 血常规子项(primary=False,走势隐藏) ====
    # 血小板系
    "血小板比积": CanonTerm("血小板比积（PCT）", primary=False),
    "血小板比容": CanonTerm("血小板比积（PCT）", primary=False),
    "血小板压积": CanonTerm("血小板比积（PCT）", primary=False),
    "血小板平均体积": CanonTerm("血小板平均体积（MPV）", primary=False),
    "平均血小板体积": CanonTerm("血小板平均体积（MPV）", primary=False),
    "血小板平均容积": CanonTerm("血小板平均体积（MPV）", primary=False),
    "血小板分布宽度": CanonTerm("血小板分布宽度（PDW）", primary=False),
    "血小板体积分布宽度": CanonTerm("血小板分布宽度（PDW）", primary=False),
    "大血小板比率": CanonTerm("大血小板比率（P-LCR）", primary=False),
    "大血小板数": CanonTerm("大血小板比率（P-LCR）", primary=False),
    # 红细胞系
    "红细胞压积": CanonTerm("红细胞压积（HCT）", primary=False),
    "红细胞比容": CanonTerm("红细胞压积（HCT）", primary=False),
    "红细胞比积": CanonTerm("红细胞压积（HCT）", primary=False),
    "平均红细胞体积": CanonTerm("平均红细胞体积（MCV）", primary=False),
    "红细胞平均体积": CanonTerm("平均红细胞体积（MCV）", primary=False),
    "平均红细胞容积": CanonTerm("平均红细胞体积（MCV）", primary=False),
    "平均红细胞血红蛋白量": CanonTerm("平均红细胞血红蛋白量（MCH）", primary=False),
    "平均红细胞血红蛋白含量": CanonTerm("平均红细胞血红蛋白量（MCH）", primary=False),
    "平均血红蛋白量": CanonTerm("平均红细胞血红蛋白量（MCH）", primary=False),
    "平均血红蛋白含量": CanonTerm("平均红细胞血红蛋白量（MCH）", primary=False),
    "平均红细胞血红蛋白浓度": CanonTerm("平均红细胞血红蛋白浓度（MCHC）", primary=False),
    "平均血红蛋白浓度": CanonTerm("平均红细胞血红蛋白浓度（MCHC）", primary=False),
    # RDW:CV 与 SD 各自成子项(带码整名先命中)
    "红细胞分布宽度（CV）": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度(CV)": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度-变异系数": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度变异系数": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度（SD）": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    "红细胞分布宽度(SD)": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    "红细胞分布宽度-标准差": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    "红细胞分布宽度标准差": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    # canonical 标准名自解析:回填/重归一化时 RDW-CV/SD 不得回落通用 RDW
    "红细胞分布宽度（RDW-CV）": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度(RDW-CV)": CanonTerm("红细胞分布宽度（RDW-CV）", primary=False),
    "红细胞分布宽度（RDW-SD）": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    "红细胞分布宽度(RDW-SD)": CanonTerm("红细胞分布宽度（RDW-SD）", primary=False),
    # 无码区分不定的 RDW 变体,归为通用 RDW 子项
    "红细胞体积分布宽度": CanonTerm("红细胞分布宽度（RDW）", primary=False),
    "红细胞分布宽度": CanonTerm("红细胞分布宽度（RDW）", primary=False),
    # NRBC
    "有核红细胞百分比": CanonTerm("有核红细胞计数（NRBC）", primary=False),
    "有核红细胞数": CanonTerm("有核红细胞计数（NRBC）", primary=False),
    "有核红细胞计数": CanonTerm("有核红细胞计数（NRBC）", primary=False),
    # 血脂子项
    "小而密低密度脂蛋白胆固醇": CanonTerm("小而密低密度脂蛋白胆固醇（sdLDL）", primary=False),
}


def _resolve(raw_name: str) -> Optional[CanonTerm]:
    cleaned = _clean(raw_name)
    if not cleaned:
        return None
    term = _ALIASES.get(cleaned)
    if term is not None:
        return term
    base = _base(cleaned)
    if base != cleaned:
        return _ALIASES.get(base)
    return None


def resolve_canonical(raw_name: str) -> Optional[CanonTerm]:
    """raw 整名 → CanonTerm;未命中(含空串/纯括号)返回 None。"""
    return _resolve(raw_name)


def is_child_item(item_name: str) -> bool:
    """raw 名解析为 primary=False 的子项 → True(指标走势隐藏子项)。"""
    if not item_name:
        return False
    term = _resolve(item_name)
    return bool(term and not term.primary)


def normalize_item_name(raw_name: str) -> tuple:
    """名称标准化。命中别名 → canonical(索引0);未命中 → raw_name.strip() 透传。"""
    raw_name = (raw_name or "").strip()
    if not raw_name:
        return "", None
    term = _resolve(raw_name)
    if term:
        return term.standard, None
    return raw_name, None


def normalize_indicators(indicators: list[dict]) -> list[dict]:
    """名称标准化 + 去重。

    体检 PDF 通常在多个章节(主检报告 / 医学科普 / 分项报告)逐一列出同一指标的同一
    数值;LLM 抽取时按章节各返回一条,DB 入库后会出现同名同值的多行。run_rules →
    filter_abnormal 会忠实于 DB 行数,导致 agent_search_knowledge 收到重复指标名、
    发重复 search_knowledge 调用、judge 也对重复指标重复审核。在此按
    (item_name_standard 或 item_name, result) 去重,保留首次出现,顺序不变。
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
