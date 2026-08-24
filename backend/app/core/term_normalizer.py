import logging
import re
from typing import Optional, Dict, Callable

from app.core.standard_catalog import STANDARD_NAMES

logger = logging.getLogger(__name__)

_RANGE_PATTERN = re.compile(r"([\d.]+)\s*[-~—到至]\s*([\d.]+)")

# === 标准别名表(子串匹配, dict 保序: 长别名在前, 短别名在后) ===
# 2026-08-18 起以《体检项目指标标准表》为权威标准名(半角括号, 见 standard_catalog.py)。
# 标准表命中的 → 标准表名; 标准表没有的 → 保留报告原文名(不强行归一化)。
# 回退: git 历史版本为旧全角括号手工别名表。
_STANDARD_MAP: Dict[str, str] = {
    # === 尿检带"尿"前缀的长别名(必须在短名之前, 避免被"葡萄糖/白细胞/红细胞"误吞) ===
    "尿微量白蛋白浓度": "尿微量蛋白(UMA)",
    "尿微量白蛋白": "尿微量蛋白(UMA)",
    "尿核基质蛋白(NMP22)测定": "尿核基质蛋白(NMP22)测定",
    "尿核基质蛋白（NMP22）测定": "尿核基质蛋白(NMP22)测定",
    "尿亚硝酸盐": "亚硝酸盐(NIT)",
    "尿胆原定性": "尿胆原(阴性、阳性、弱阳性)",
    "尿胆红素": "尿胆红素(BIL)",
    "尿酮体": "尿酮体(KET)",
    "尿蛋白": "尿蛋白(PRO)",
    "尿潜血": "尿潜血(BLD)",
    "尿葡萄糖": "尿葡萄糖(GLU)",
    "尿白细胞": "尿白细胞(LEU)",
    "尿红细胞": "尿红细胞(镜检)",
    # === 血常规/血生化别名 ===
    "全血糖化血红蛋白测定": "糖化血红蛋白",
    "糖化血红蛋白": "糖化血红蛋白",
    "平均红细胞血红蛋白含量": "平均RBC血红蛋白(MCH)",
    "平均红细胞血红蛋白量": "平均RBC血红蛋白(MCH)",
    "平均红细胞血红蛋白浓度": "平均红细胞血红蛋白浓度(MCHC)",
    "红细胞分布宽度（CV）": "红细胞变异系数(RDW-CV)",
    "红细胞分布宽度（SD）": "红细胞分布宽度(RDW-SD)",
    "红细胞分布宽度(CV)": "红细胞变异系数(RDW-CV)",
    "红细胞分布宽度(SD)": "红细胞分布宽度(RDW-SD)",
    "红细胞压积": "红细胞压积(HCT)",
    "平均红细胞体积": "红细胞平均体积(MCV)",
    "红细胞平均体积": "红细胞平均体积(MCV)",
    "血小板体积分布宽度": "血小板分布宽度(PDW)",
    "血小板分布宽度": "血小板分布宽度(PDW)",
    "血小板平均体积": "血小板平均容积(MPV)",
    "平均血小板体积": "血小板平均容积(MPV)",
    "血小板压积": "血小板压积(PCT)",
    "血小板比积": "血小板压积(PCT)",
    "血小板计数": "血小板数(PLT)",
    "大血小板比率": "大血小板比例(P-LCR)",
    "有核红细胞百分比": "有核红细胞百分比",
    "有核红细胞绝对值": "有核红细胞绝对值",
    "中性粒细胞绝对值": "中性粒细胞绝对值(NEUT#)",
    "中性粒细胞计数": "中性粒细胞绝对值(NEUT#)",
    "淋巴细胞绝对值": "淋巴细胞绝对值(LYM#)",
    "淋巴细胞计数": "淋巴细胞绝对值(LYM#)",
    "单核细胞绝对值": "单核细胞绝对值(MONO#)",
    "单核细胞计数": "单核细胞绝对值(MONO#)",
    "嗜酸粒细胞绝对值": "嗜酸细胞绝对值(EO#)",
    "嗜酸性细胞计数": "嗜酸细胞绝对值(EO#)",
    "嗜酸粒细胞百分比": "嗜酸性细胞比例(EO)",
    "嗜酸性细胞百分率": "嗜酸性细胞比例(EO)",
    "嗜酸粒细胞百分率": "嗜酸性细胞比例(EO)",
    "嗜碱粒细胞绝对值": "嗜碱细胞绝对值(BASO#)",
    "嗜碱性细胞计数": "嗜碱细胞绝对值(BASO#)",
    "嗜碱粒细胞百分比": "嗜碱性细胞比例(BASO)",
    "嗜碱性细胞百分率": "嗜碱性细胞比例(BASO)",
    "中性粒细胞百分比": "中性粒细胞百分比",
    "中性粒细胞百分率": "中性粒细胞百分比",
    "淋巴细胞百分比": "淋巴细胞百分比(LYM)",
    "淋巴细胞百分率": "淋巴细胞百分比(LYM)",
    "单核细胞百分比": "单核细胞百分比(MONO%)",
    "单核细胞百分率": "单核细胞百分比(MONO%)",
    "血红蛋白": "血红蛋白(HGB)",
    "白细胞数": "白细胞数(WBC)",
    "白细胞计数": "白细胞数(WBC)",
    "红细胞数": "红细胞数(RBC)",
    "红细胞计数": "红细胞数(RBC)",
    # === 生化别名 ===
    "谷氨酰转酞酶": "谷氨酰转移酶(r-GT)",
    "γ-谷氨酰转肽酶": "谷氨酰转移酶(r-GT)",
    "γ-谷氨酰转移酶": "谷氨酰转移酶(r-GT)",
    "谷草/谷丙": "谷草/谷丙",
    "谷丙转氨酶": "丙氨酸氨基转移酶(谷丙酶)",
    "谷草转氨酶": "天门冬氨酸氨基转移酶(谷草酶)",
    "总胆红质": "总胆红素(TBIL)",
    "总胆红素": "总胆红素(TBIL)",
    "直接胆红质": "直接胆红素(DBIL)",
    "直接胆红素": "直接胆红素(DBIL)",
    "间接胆红素": "间接胆红素(IBIL)",
    "总胆汁酸": "总胆汁酸",
    "白球比值": "白球比值",
    "前白蛋白": "前白蛋白",
    "总蛋白": "总蛋白(TP)",
    "白蛋白": "白蛋白(ALB)",
    "球蛋白": "球蛋白",
    "胆碱脂酶": "胆碱脂酶",
    "肌酐(酶法)": "肌酐",
    "肌酐": "肌酐",
    "尿素氮": "尿素(BUN)",
    "尿素": "尿素(BUN)",
    "高密度脂蛋白胆固醇": "高密度脂蛋白胆固醇(HDL)",
    "高密度脂蛋白": "高密度脂蛋白胆固醇(HDL)",
    "小而密低密度脂蛋白胆固醇": "小而密低密度脂蛋白胆固醇",
    "低密度脂蛋白胆固醇": "低密度脂蛋白胆固醇(LDL)",
    "低密度脂蛋白": "低密度脂蛋白胆固醇(LDL)",
    "总胆固醇": "总胆固醇(CHOL)",
    "甘油三酯": "甘油三酯(TG)",
    "肌酸激酶": "肌酸激酶(CK)",
    "血肌酸激酶": "肌酸激酶(CK)",
    "CK同工酶(质量)": "肌酸激酶MB型同工酶(CK-MB)",
    "CK 同工酶(质量)": "肌酸激酶MB型同工酶(CK-MB)",
    "心肌肌钙蛋白I": "心肌肌钙蛋白I",
    "乳酸脱氢酶": "乳酸脱氢酶(LDH)",
    "a-羟丁酸脱氢酶": "α-羟丁酸脱氢酶",
    "α-羟丁酸脱氢酶": "α-羟丁酸脱氢酶",
    "超氧化物歧化酶": "超氧化物歧化酶",
    "二氧化碳": "二氧化碳(CO2)",
    "阴离子间隙": "阴离子间隙",
    "渗透压": "渗透压",
    "尿酸": "尿酸(UA)",
    "血沉": "血沉",
    "钾": "钾(K)",
    "钠": "钠(Na)",
    "氯": "氯(Cl)",
    "钙": "钙(Ca)",
    "无机磷": "无机磷",
    "磷": "无机磷",
    "镁": "镁",
    # === 甲功(游离必须在总前, 避免"游离甲状腺素"被"甲状腺素"吞) ===
    "促甲状腺激素(TSH)测定": "促甲状腺激素(TSH)",
    "促甲状腺素": "促甲状腺激素(TSH)",
    "游离三碘甲状腺原氨酸(FT3)测定": "游离三碘甲状腺原氨酸(FT3)",
    "游离三碘甲状腺原氨酸": "游离三碘甲状腺原氨酸(FT3)",
    "游离T3": "游离三碘甲状腺原氨酸(FT3)",
    "三碘甲状腺原氨酸": "总三碘甲状腺原氨酸(T3)",
    "游离甲状腺素(FT4)测定": "游离甲状腺素(FT4)",
    "游离甲状腺素": "游离甲状腺素(FT4)",
    "游离T4": "游离甲状腺素(FT4)",
    "甲状腺素": "总甲状腺原氨酸(T4)",
    "甲状腺摄取率": "甲状腺摄取率",
    # === 肿瘤标志物 ===
    "癌胚抗原": "癌胚抗原(CEA)定量",
    "甲胎蛋白": "甲胎蛋白(AFP)定量",
    "糖原蛋白125": "CA125",
    "糖原蛋白153": "CA153",
    "癌抗原CA19-9": "CA-199",
    "神经元特异性烯醇化酶": "神经元特异性烯醇化酶",
    "CA72-4": "CA724",
    "CA724": "CA724",
    "鳞状细胞癌相关抗原": "鳞状细胞癌相关抗原 (SCC)",
    "细胞角蛋白19片段": "细胞角蛋白19片段",
    "EB病毒": "EB病毒抗体",
    "膀胱癌尿FISH": "膀胱癌尿FISH测定",
    "膀胱癌尿FISH测定": "膀胱癌尿FISH测定",
    "D-二聚体": "D-二聚体",
    "利钠肽": "利钠肽(BNP)",
    "NT-ProBNP": "神经末端利钠肽原(NT-ProBNP)",
    "BNP": "利钠肽(BNP)",
    "超敏肌钙蛋白": "超敏肌钙蛋白(hs-cTn)",
    "胱抑素C": "胱抑素C",
    "尿蛋白/尿肌酐比值": "尿蛋白/尿肌酐比值",
    "尿蛋白/肌酐": "尿蛋白/尿肌酐比值",
    "抗过氧化物酶抗体": "抗过氧化物酶抗体(TR-Ab)",
    "TPOAb": "抗过氧化物酶抗体(TR-Ab)",
    "抗球蛋白抗体": "抗球蛋白抗体(TG)",
    "TGAb": "抗球蛋白抗体(TG)",
    "载脂蛋白A1": "血清载脂蛋白A",
    "载脂蛋白A": "血清载脂蛋白A",
    "血清载脂蛋白A": "血清载脂蛋白A",
    "载脂蛋白B": "血清载脂蛋白B",
    "血清载脂蛋白B": "血清载脂蛋白B",
    "游离前列腺特异性抗原": "游离前列腺特异性抗原(FPSA)",
    "前列腺特异性抗原": "前列腺特异性抗原(PSA)",
    "肿瘤特异生长因子": "肿瘤特异生长因子",
    # === 查体 ===
    "体重指数": "体重指数",
    "体质指数": "体重指数",
    "收缩压": "收缩压(高压)",
    "舒张压": "舒张压(低压)",
    "心率": "心率",
    "眼压右": "眼压右",
    "眼压左": "眼压左",
    "矫正视力(右)": "右眼矫正视力",
    "裸眼视力右": "右眼视力",
    "矫正视力(左)": "左眼矫正视力",
    "裸眼视力左": "左眼视力",
    # === 结论条目(保留部位, 供 disease_mapping 链接) ===
    "甲状腺双叶多发囊性结节": "甲状腺囊性结节",
    "右肺尖间隔旁型肺气肿": "肺气肿（间隔旁型）",
    "同型半胱氨酸": "同型半胱氨酸",
    # === 结论条目(2026-08-19, 高检出率慢病检查结论短语, 供 disease_mapping 链接) ===
    # 长短语在前, 避免被短别名误吞; 与 engine 结论型子串匹配双保险。
    "颈动脉内膜增厚": "颈动脉粥样硬化",
    "颈动脉斑块": "颈动脉粥样硬化",
    "颈动脉粥样硬化": "颈动脉粥样硬化",
    "骨量减少": "骨质疏松",
    "骨质疏松": "骨质疏松",
    "幽门螺旋杆菌": "幽门螺杆菌感染",
    "幽门螺杆菌": "幽门螺杆菌感染",
    "前列腺肥大": "前列腺增生",
    "前列腺增生": "前列腺增生",
    "乳腺小叶增生": "乳腺增生",
    "乳腺增生": "乳腺增生",
    "子宫平滑肌瘤": "子宫肌瘤",
    "子宫肌瘤": "子宫肌瘤",
    "卵巢囊肿": "卵巢囊肿",
    "胆结石": "胆囊结石",
    "胆囊结石": "胆囊结石",
    "肾结石": "肾结石",
    "脂肪浸润": "脂肪肝",
    "重度脂肪肝": "脂肪肝",
    "中度脂肪肝": "脂肪肝",
    "轻度脂肪肝": "脂肪肝",
    "脂肪肝": "脂肪肝",
    # 注意: 不做裸"囊性结节"别名 —— "乳腺囊性结节"等跨器官短语会被误指为甲状腺。
    "甲状腺囊性结节": "甲状腺囊性结节",
    "甲状腺混合性结节": "甲状腺混合性结节",
    "甲状腺实性结节": "甲状腺实性结节",
    "甲状腺结节": "甲状腺结节",
    "乙肝表面抗原": "乙肝表面抗原(HBsAg)",
    # === 尿检短别名(必须位于含其子串的长别名之后, 如"胆红素"在"总胆红素"后) ===
    "蛋白质": "尿蛋白(PRO)",
    "潜血": "尿潜血(BLD)",
    "酮体": "尿酮体(KET)",
    "胆红素": "尿胆红素(BIL)",
    "亚硝酸盐": "亚硝酸盐(NIT)",
    # === 现有保留别名 ===
    "血糖": "空腹血糖",
}

# 同名跨科目消歧(血检 vs 尿检): 依据 result/unit 特征。无上下文时回退默认(血检口径)
def _is_qualitative(v: Optional[str]) -> bool:
    return bool(v) and str(v).strip() in ("阴性", "-", "+-", "阳性", "弱阳性", "未见")


def _glucose(result, unit) -> str:
    if _is_qualitative(result):
        return "尿葡萄糖(GLU)"
    if unit and "mmol" in str(unit).lower():
        return "空腹血糖"
    return "空腹血糖"


def _wbc(result, unit) -> str:
    if _is_qualitative(result) or (unit and ("HP" in str(unit) or "/μL" in str(unit))):
        return "尿白细胞(LEU)"
    # 白带镜检(标准表"白细胞" unit=个/视野)
    if unit and ("个" in str(unit) or "视野" in str(unit)):
        return "白细胞"
    return "白细胞数(WBC)"


def _rbc(result, unit) -> str:
    if unit and "个" in str(unit):
        return "红细胞"
    if _is_qualitative(result) or (unit and "/μL" in str(unit)):
        return "尿红细胞(镜检)"
    return "红细胞数(RBC)"


def _rdw(result, unit) -> str:
    if unit and "%" in str(unit):
        return "红细胞变异系数(RDW-CV)"
    return "红细胞分布宽度(RDW-SD)"


def _urobilinogen(result, unit) -> str:
    if _is_qualitative(result):
        return "尿胆原(阴性、阳性、弱阳性)"
    return "尿胆原(数值)"


_AMBIGUOUS_MAP: Dict[str, Callable] = {
    # 特异的先查(避免"红细胞"裸名把 红细胞压积/RDW 等全吞成红细胞数):
    "红细胞体积分布宽度": _rdw,
    "红细胞分布宽度": _rdw,
    "尿胆原": _urobilinogen,
    # 裸名消歧(血检 vs 尿检)最后查:
    "葡萄糖": _glucose,
    "白细胞": _wbc,
    "红细胞": _rbc,
}


def normalize_item_name(raw_name: str, result: Optional[str] = None,
                        unit: Optional[str] = None) -> tuple:
    """名称标准化: 标准表/别名表命中 → 标准表名; 未命中 → 保留原文名。

    同名跨科目指标(葡萄糖/白细胞/红细胞/红细胞分布宽度/尿胆原)按 result/unit 消歧。
    """
    cleaned = raw_name.strip().replace(" ", "").replace("　", "")
    if not cleaned:
        return cleaned, None
    # === STRATEGY:v2026-08-18-normalize-order 消歧/幂等顺序 ===
    # 匹配顺序(2026-08-18 定稿):
    #   1. 标准名幂等 —— 归一化输出(如"红细胞平均体积(MCV)")再输入原样返回,
    #      防短别名("红细胞")或含"钙"的结论条目("肝内钙化灶")误吞。
    #      例外: 标准表 1267/1268 行的"白细胞/红细胞"是白带镜检项(unit=个/视野),
    #      需放行到裸名消歧按单位区分(血/尿/白带口径)。
    #   2. 裸名精确消歧: 输入整名为"葡萄糖/白细胞/红细胞"时按 result/unit 消歧。
    #   3. 特异子串消歧: "红细胞分布宽度/红细胞体积分布宽度/尿胆原"。
    #   4. 别名表子串匹配。
    # 回退: git 历史版本为旧顺序(消歧在前, 会吞掉含"红细胞"的所有指标)。
    if cleaned in STANDARD_NAMES and cleaned not in ("白细胞", "红细胞"):
        return cleaned, None
    if cleaned in _AMBIGUOUS_MAP:
        return _AMBIGUOUS_MAP[cleaned](result, unit), None
    for alias, fn in _AMBIGUOUS_MAP.items():
        # 裸名(葡萄糖/白细胞/红细胞)只做整名精确匹配(上一步已处理), 不做子串
        if alias in ("葡萄糖", "白细胞", "红细胞"):
            continue
        if alias in cleaned:
            return fn(result, unit), None
    # === END STRATEGY ===
    for alias, standard in _STANDARD_MAP.items():
        if alias in cleaned:
            # === STRATEGY:v2026-08-18-short-alias-exact 单字别名仅精确匹配 ===
            # "钙/钾/钠/氯/磷/镁"等单字别名若做子串匹配, 会吞掉"肝内钙化灶"等结论条目。
            # 回退: 删除本判断即恢复旧行为。
            if len(alias) <= 1 and cleaned != alias:
                continue
            # === END STRATEGY ===
            return standard, None
    return cleaned, None


def normalize_indicators(indicators: list[dict]) -> list[dict]:
    """名称标准化 + 参考范围拆分 + 去重。

    体检 PDF 通常在多个章节逐一列出同一指标的同一数值;LLM 抽取时按章节各返回一条,
    DB 入库后会出现同名同值的多行。在此按 (item_name_standard 或 item_name, result) 去重。
    """
    for ind in indicators:
        name, code = normalize_item_name(ind.get("item_name", ""),
                                         ind.get("result", ""),
                                         ind.get("unit", ""))
        ind["item_name_standard"] = name
        ind["item_code"] = code
        _split_ref_range(ind)

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


def _split_ref_range(ind: dict) -> None:
    """当 ref_low 包含完整范围字符串(如 0.18-0.22)且 ref_high 为空时拆分。"""
    low = ind.get("ref_low")
    high = ind.get("ref_high")
    if low and not high:
        m = _RANGE_PATTERN.match(str(low).strip())
        if m:
            ind["ref_low"] = m.group(1)
            ind["ref_high"] = m.group(2)
