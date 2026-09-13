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
    "葡萄糖(尿)": "尿葡萄糖(GLU)",
    "白细胞(尿)": "尿白细胞(LEU)",
    "红细胞(尿)": "尿红细胞(镜检)",
    "尿隐血": "尿潜血(BLD)",
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
    "红细胞比容": "红细胞压积(HCT)",
    "红细胞比积": "红细胞压积(HCT)",
    "平均红细胞体积": "红细胞平均体积(MCV)",
    "红细胞平均体积": "红细胞平均体积(MCV)",
    "血小板体积分布宽度": "血小板分布宽度(PDW)",
    "血小板分布宽度": "血小板分布宽度(PDW)",
    "血小板平均体积": "血小板平均容积(MPV)",
    "平均血小板体积": "血小板平均容积(MPV)",
    "血小板压积": "血小板压积(PCT)",
    "血小板比容": "血小板压积(PCT)",
    "血小板比积": "血小板压积(PCT)",
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
    "γ-谷氨酰基转移酶": "谷氨酰转移酶(r-GT)",
    "谷氨酰基转移酶": "谷氨酰转移酶(r-GT)",
    "谷氨酰转肽酶": "谷氨酰转移酶(r-GT)",
    "谷草/谷丙": "谷草/谷丙",
    "白/球比值": "白球比值",
    "肝内钙化点": "肝内钙化灶",
    "大型血小板比率": "大血小板比例(P-LCR)",
    "a羟基丁酸脱氢酶": "α-羟丁酸脱氢酶",
    "糖类抗原19-9": "CA-199",
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
    "肌钙蛋白I": "心肌肌钙蛋白I",
    "超敏肌钙蛋白": "超敏肌钙蛋白(hs-cTn)",
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
    "游离甲状腺激素": "游离甲状腺素(FT4)",
    "甲状腺素": "总甲状腺原氨酸(T4)",
    "甲状腺摄取率": "甲状腺摄取率",
    # === 肿瘤标志物 ===
    "癌胚抗原": "癌胚抗原(CEA)定量",
    "甲胎蛋白": "甲胎蛋白(AFP)定量",
    "糖原蛋白125": "CA125",
    "糖原蛋白153": "CA153",
    "糖类抗原CA199": "CA-199",
    "糖类抗原CA125": "CA125",
    "糖类抗原CA153": "CA153",
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
    "总前列腺特异抗原": "总前列腺特异性抗原(TPSA)",
    "游离前列腺特异抗原": "游离前列腺特异性抗原(FPSA)",
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
    # 2026-08-26: 幽门螺杆菌分型抗体为独立检测项, 不得吞入"幽门螺杆菌感染"
    # (长别名在前, 优先于短名"幽门螺杆菌"包含匹配)
    "幽门螺杆菌尿素酶抗体": "幽门螺杆菌尿素酶抗体",
    "幽门螺杆菌细胞毒素抗体": "幽门螺杆菌细胞毒素抗体",
    "幽门螺杆菌空泡毒素抗体": "幽门螺杆菌空泡毒素抗体",
    "幽门螺杆菌空炮毒素抗体": "幽门螺杆菌空泡毒素抗体",
    "幽门螺杆菌分型幽门螺杆菌抗体I型": "幽门螺杆菌抗体I型",
    "幽门螺杆菌分型幽门螺杆菌抗体II型": "幽门螺杆菌抗体II型",
    "幽门螺杆菌抗体I型": "幽门螺杆菌抗体I型",
    "幽门螺杆菌抗体II型": "幽门螺杆菌抗体II型",
    "幽门螺杆菌分型": "幽门螺杆菌分型",
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
    "甲状腺混合性结节": "甲状腺结节",
    "甲状腺实性结节": "甲状腺结节",
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


def _clean_textual_noise(s: str) -> str:
    """方案1: 文本清洗层(2026-08-24, 广西多院测试暴露)。

    LLM 输出指标名带排版噪声: 全角连字符/括号、空格、前后缀。
    纯规则统一书写, 不改变语义; 与标准目录(半角括号)对齐。
    - 全角连字符/括号/标点 → 半角
    - 去空格(全角/半角)
    """
    _FIX = {"－": "-", "（": "(", "）": ")", "：": ":", "，": ",", "．": "."}
    for a, b in _FIX.items():
        s = s.replace(a, b)
    return s.strip().replace(" ", "").replace("\u3000", "")


# 剥头部前缀(长词优先, 防"血小板"被"血"误剥): 剥后必须命中才采用(见 _match_with_fallback)
_AFFIX_PREFIX_RE = re.compile(r"^(血清|血浆|全血|血)")
# 剥尾部后缀: 不剥"定量"(标准名含"癌胚抗原(CEA)定量"), 仅剥测量动作词;
# 允许动作词后跟括号修饰("葡萄糖测定(空腹)"→"葡萄糖")
_AFFIX_SUFFIX_RE = re.compile(r"(测定|检测|试验|检查)(\([^)]*\))?$")


def _strip_affixes(s: str) -> str:
    s = _AFFIX_SUFFIX_RE.sub("", s)
    return _AFFIX_PREFIX_RE.sub("", s)


# === 方案2: 结构化匹配(2026-08-24, 广西多院测试暴露) ===
# 枚举别名追不完 LLM 变体; 改为拆解比对:
#   1) 主名匹配: 标准名去掉括号后的主名(如"天门冬氨酸氨基转移酶(谷草酶)"→主名)
#      与输入剥括号后全等 → 命中, 覆盖"无括号变体"类 MISS。
#   2) 量词正则: 血细胞 5 系 × (数/值/绝对值/计数 → 绝对值口径; 比率/百分数/百分比/百分率 → 百分比口径)。
#   3) 解剖限定剥离: 左/右/叶/双侧 等词剥除后命中才采用(甲状腺左叶结节 → 甲状腺结节)。
#   4) 同义前缀替换: 仅确定同义词(乙型肝炎→乙肝)。
_PAREN_RE = re.compile(r"\([^)]*\)")
_QUANT_PAREN_RE = re.compile(r"\((绝对值|绝对数|百分比|百分数|比率|比值|计数)\)")
_ANATOMY_WORDS = ("左叶", "右叶", "双侧", "左", "右", "双眼", "单眼")
_SYNONYM_SUB = (("乙型肝炎", "乙肝"),)

_CELL_ABS_RE = re.compile(
    r"^(中性|淋巴|单核|嗜酸|嗜酸性|嗜碱|嗜碱性)(?:粒细胞|细胞)?(数|值|数目|绝对数|绝对值|计数)$")
_CELL_PCT_RE = re.compile(
    r"^(中性|淋巴|单核|嗜酸|嗜酸性|嗜碱|嗜碱性)(?:粒细胞|细胞)?(比率|比值|百分数|百分比|百分率)$")
_CELL_ABS_MAP = {
    "中性": "中性粒细胞绝对值(NEUT#)", "淋巴": "淋巴细胞绝对值(LYM#)",
    "单核": "单核细胞绝对值(MONO#)", "嗜酸": "嗜酸细胞绝对值(EO#)",
    "嗜碱": "嗜碱细胞绝对值(BASO#)",
}
_CELL_PCT_MAP = {
    "中性": "中性粒细胞百分比", "淋巴": "淋巴细胞百分比(LYM)",
    "单核": "单核细胞百分比(MONO%)", "嗜酸": "嗜酸性细胞比例(EO)",
    "嗜碱": "嗜碱性细胞比例(BASO)",
}


def _build_main_name_map() -> dict:
    """标准目录 ∪ 中央映射表的"主名"(去括号) → 标准名; 主名冲突(多目标)剔除。"""
    from collections import defaultdict
    names = set(STANDARD_NAMES)
    try:
        from app.modules.risk.seed import CENTRAL_MAPPINGS
        names |= {m[0] for m in CENTRAL_MAPPINGS}
    except Exception:
        pass
    main: dict = defaultdict(list)
    for n in names:
        m = _PAREN_RE.sub("", n).strip()
        if m:
            main[m].append(n)
    return {k: v[0] for k, v in main.items() if len(v) == 1}


_MAIN_NAME_MAP = _build_main_name_map()


# HEAD 精度补充: feat/app 子串别名表会把这些子项/同名项吞成父项,
# 在消歧/子串匹配前做精确名优先, 与 HEAD 版行为对齐。canonical 取 standard_catalog。
_PRECISE_ALIASES: Dict[str, str] = {
    "平均血红蛋白浓度": "平均红细胞血红蛋白浓度(MCHC)",
    "平均血红蛋白量": "平均RBC血红蛋白(MCH)",
    "平均血红蛋白含量": "平均RBC血红蛋白(MCH)",
    "平均红细胞容积": "红细胞平均体积(MCV)",
    "大血小板数": "大血小板比例(P-LCR)",
    "有核红细胞数": "有核红细胞绝对值",
    "有核红细胞计数": "有核红细胞绝对值",
    "尿白细胞酯酶": "尿白细胞酯酶",
    "尿酸碱度": "尿液酸碱度(PH)",
    "红细胞分布宽度(CV)": "红细胞变异系数(RDW-CV)",
    "红细胞分布宽度-变异系数": "红细胞变异系数(RDW-CV)",
    "红细胞分布宽度变异系数": "红细胞变异系数(RDW-CV)",
    "红细胞分布宽度(SD)": "红细胞分布宽度(RDW-SD)",
    "红细胞分布宽度-标准差": "红细胞分布宽度(RDW-SD)",
    "红细胞分布宽度标准差": "红细胞分布宽度(RDW-SD)",
    "红细胞分布宽度(RDW-CV)": "红细胞变异系数(RDW-CV)",
    "红细胞分布宽度(RDW-SD)": "红细胞分布宽度(RDW-SD)",
}


def _match_core(name: str, result: Optional[str], unit: Optional[str]) -> Optional[str]:
    """核心匹配链(不含解剖剥离/同义替换递归): 幂等→消歧→量词→主名→别名。"""
    if name in STANDARD_NAMES and name not in ("白细胞", "红细胞"):
        return name
    if name in _PRECISE_ALIASES:
        return _PRECISE_ALIASES[name]
    if name in _AMBIGUOUS_MAP:
        return _AMBIGUOUS_MAP[name](result, unit)
    for alias, fn in _AMBIGUOUS_MAP.items():
        if alias in ("葡萄糖", "白细胞", "红细胞"):
            continue
        if alias in name:
            return fn(result, unit)
    # 括号修饰(空腹/尿/方法学/量词等)剥除后递归走核心链:
    # "葡萄糖(空腹)"→"葡萄糖"(裸名消歧); "白细胞(WBC)"→"白细胞"(消歧)
    unparen = _PAREN_RE.sub("", name)
    if unparen != name:
        hit = _match_core(unparen, result, unit)
        if hit:
            return hit
    # 括号内量词展开: "中性粒细胞(绝对值)"→"中性粒细胞绝对值"
    name_quant = _QUANT_PAREN_RE.sub(r"\1", name)
    if name_quant != name:
        hit = _match_core(name_quant, result, unit)
        if hit:
            return hit
    m = _CELL_ABS_RE.match(_PAREN_RE.sub("", name))
    if m:
        return _CELL_ABS_MAP[m.group(1).replace("嗜酸性", "嗜酸").replace("嗜碱性", "嗜碱")]
    m = _CELL_PCT_RE.match(_PAREN_RE.sub("", name))
    if m:
        return _CELL_PCT_MAP[m.group(1).replace("嗜酸性", "嗜酸").replace("嗜碱性", "嗜碱")]
    if name in _MAIN_NAME_MAP:
        return _MAIN_NAME_MAP[name]
    for alias, standard in _STANDARD_MAP.items():
        if alias in name:
            if len(alias) <= 1 and name != alias:
                continue
            # "球蛋白"不得吞"微球蛋白"(β2-微球蛋白是肾损伤指标, 非球蛋白)
            if alias == "球蛋白" and "微球蛋白" in name:
                continue
            # "尿素"不得吞"尿素酶"(幽门螺杆菌尿素酶抗体是 Hp 检测, 非肾功能尿素)
            if alias == "尿素" and "尿素酶" in name:
                continue
            return standard
    return None


def _match(name: str, result: Optional[str], unit: Optional[str]) -> Optional[str]:
    """方案2: 核心匹配 + 解剖限定剥离/同义替换(命中才采用, 有限层)。"""
    hit = _match_core(name, result, unit)
    if hit:
        return hit
    # 解剖限定词剥离: "甲状腺左叶结节"→"甲状腺结节"
    for w in _ANATOMY_WORDS:
        cand = name.replace(w, "")
        if cand and cand != name:
            hit = _match_core(cand, result, unit)
            if hit:
                return hit
    # 同义前缀替换: "乙型肝炎表面抗原"→"乙肝表面抗原"
    for a, b in _SYNONYM_SUB:
        if a in name:
            cand = name.replace(a, b)
            if cand != name:
                hit = _match_core(cand, result, unit)
                if hit:
                    return hit
    return None


def normalize_item_name(raw_name: str, result: Optional[str] = None,
                        unit: Optional[str] = None) -> tuple:
    """名称标准化: 标准表/别名表命中 → 标准表名; 未命中 → 保留原文名。

    同名跨科目指标(葡萄糖/白细胞/红细胞/红细胞分布宽度/尿胆原)按 result/unit 消歧。

    方案1(2026-08-24): 入口先做文本清洗(全角→半角/去空格/剥前后缀),
    清洗候选(stem)匹配失败时回退未剥版本, 保证"血小板"类词不被"血"前缀误剥。
    """
    cleaned = _clean_textual_noise(raw_name)
    if not cleaned:
        return cleaned, None
    hit = _match(cleaned, result, unit)
    if hit:
        return hit, None
    stem = _strip_affixes(cleaned)
    if stem != cleaned:
        hit = _match(stem, result, unit)
        if hit:
            return hit, None
    return cleaned, None


def normalize_indicators(indicators: list[dict]) -> list[dict]:
    """名称标准化 + 参考范围拆分 + 去重。

    体检 PDF 通常在多个章节(主检报告 / 医学科普 / 分项报告)逐一列出同一指标的同一
    数值;LLM 抽取时按章节各返回一条,DB 入库后会出现同名同值的多行。run_rules →
    filter_abnormal 会忠实于 DB 行数,导致 agent_search_knowledge 收到重复指标名、
    发重复 search_knowledge 调用、judge 也对重复指标重复审核。在此按
    (item_name_standard 或 item_name, result) 去重,保留首次出现,顺序不变。
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
            # 2026-08-29: 同 key 去重时保留 signal_flag 更高者(标志行优先于无标志行)
            for existing in deduped:
                ekey = (
                    existing.get("item_name_standard") or existing.get("item_name", ""),
                    str(existing.get("result", "") or "").strip(),
                )
                if ekey == key and (ind.get("signal_flag") or 0) > (existing.get("signal_flag") or 0):
                    deduped.remove(existing)
                    deduped.append(ind)
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


# ==== HEAD 兼容 API(CanonTerm/is_child_item/resolve_canonical) ====
# 仅保留给旧调用方/测试; normalize_item_name/normalize_indicators 以上面 feat/app 版为准。
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple



@dataclass(frozen=True)
class CanonTerm:
    """词条。standard 为标准名;primary=False 表示子项/衍生物(仅分类标记,当前展示层不再据此过滤)。"""
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

    # ==== 血常规子项(primary=False 分类标记,当前展示层不再据此过滤) ====
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
    """raw 名解析为 primary=False 的子项 → True(分类查询用;当前 profile 展示层不使用)。"""
    if not item_name:
        return False
    term = _resolve(item_name)
    return bool(term and not term.primary)


