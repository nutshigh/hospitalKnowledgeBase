"""院方标准指标表归一化测试(2026-08-18)。

覆盖:
1. 标准表 catalog 与《体检项目指标标准表.md》一致
2. 两机构(H003/H004)报告指标可映射到标准表名或保留原名
3. risk seed 映射名与归一化幂等
4. 关键消歧与防误吞回归
"""
import re
from pathlib import Path

import pytest

from app.core.standard_catalog import STANDARD_NAMES
from app.core.term_normalizer import normalize_item_name
from app.modules.risk.seed import CENTRAL_MAPPINGS, CENTRAL_RULES

_STD_TABLE = Path(__file__).resolve().parents[2] / "体检项目指标标准表.md"


def _parse_std_table():
    names = set()
    for line in _STD_TABLE.read_text().splitlines():
        m = re.match(
            r"\|\s*(\d+)\s*\|\s*(\d*)\s*\|\s*(\d*)\s*\|\s*(\d*)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(\d+)\s*\|",
            line,
        )
        if m and m.group(1).isdigit():
            names.add(re.sub(r"(男|女)$", "", m.group(5).strip()))
    return names


def test_standard_catalog_matches_table():
    assert set(_parse_std_table()) == STANDARD_NAMES


def test_seed_names_idempotent():
    """seed 里的映射名必须是归一化的不动点(保证 risk 匹配链一致)。"""
    for std, *_ in CENTRAL_MAPPINGS:
        n, _ = normalize_item_name(std)
        assert n == std, f"seed name not idempotent: {std} -> {n}"
    for _, _, _, _, members, _ in CENTRAL_RULES:
        for m in members:
            n, _ = normalize_item_name(m["name"])
            assert n == m["name"], f"rule member not idempotent: {m['name']} -> {n}"


@pytest.mark.parametrize("raw,result,unit,expected", [
    # 标准表名映射
    ("谷草转氨酶", "14.6", "U/L", "天门冬氨酸氨基转移酶(谷草酶)"),
    ("谷丙转氨酶", "14", "U/L", "丙氨酸氨基转移酶(谷丙酶)"),
    ("收缩压", "106", "mmHg", "收缩压(高压)"),
    ("舒张压", "65", "mmHg", "舒张压(低压)"),
    ("体质指数", "26.38", "kg/m^2", "体重指数"),
    ("血红蛋白", "144", "g/L", "血红蛋白(HGB)"),
    ("血糖", "5.16", "mmol/L", "空腹血糖"),
    ("磷", "1.31", "mmol/L", "无机磷"),
    ("二氧化碳", "25.2", "mmol/L", "二氧化碳(CO2)"),
    ("肌酐(酶法)", "63.6", "umol/L", "肌酐"),
    ("尿素", "5.45", "mmol/L", "尿素(BUN)"),
    ("肌酸激酶", "64", "U/L", "肌酸激酶(CK)"),
    ("癌胚抗原", "0.84", "ng/ml", "癌胚抗原(CEA)定量"),
    ("糖原蛋白125", "9.37", "U/ml", "CA125"),
    ("游离T3", "2.89", "pg/mL", "游离三碘甲状腺原氨酸(FT3)"),
    ("甲状腺素", "84.24", "ng/mL", "总甲状腺原氨酸(T4)"),
    ("总胆固醇", "4.96", "mmol/L", "总胆固醇(CHOL)"),
    ("谷氨酰转酞酶", "20", "U/L", "谷氨酰转移酶(r-GT)"),
    # 同名消歧(血检 vs 尿检)
    ("红细胞", "4.66", "10~12/L", "红细胞数(RBC)"),
    ("红细胞", "5", "/μL", "尿红细胞(镜检)"),
    ("白细胞", "5.53", "10~9/L", "白细胞数(WBC)"),
    ("白细胞", "6", "/μL", "尿白细胞(LEU)"),
    ("葡萄糖", "5.16", "mmol/L", "空腹血糖"),
    ("葡萄糖", "-", "mmol/L", "尿葡萄糖(GLU)"),
    # RDW 按单位消歧
    ("红细胞体积分布宽度", "12.20", "%", "红细胞变异系数(RDW-CV)"),
    ("红细胞体积分布宽度", "42.50", "fL", "红细胞分布宽度(RDW-SD)"),
    ("平均红细胞体积", "94.00", "fL", "红细胞平均体积(MCV)"),
    ("红细胞压积", "43.8", "%", "红细胞压积(HCT)"),
    # 尿胆原定性/数值
    ("尿胆原", "+-", "μmol/L", "尿胆原(阴性、阳性、弱阳性)"),
    ("尿胆原", "0.2", "umol/L", "尿胆原(数值)"),
    # 标准名幂等(防短别名误吞)
    ("红细胞平均体积(MCV)", None, None, "红细胞平均体积(MCV)"),
    ("平均RBC血红蛋白(MCH)", None, None, "平均RBC血红蛋白(MCH)"),
    ("总前列腺特异性抗原(TPSA)", None, None, "总前列腺特异性抗原(TPSA)"),
    ("肌酸激酶MB型同工酶(CK-MB)", None, None, "肌酸激酶MB型同工酶(CK-MB)"),
    # 结论条目/单字别名防误吞
    ("肝内钙化灶", None, None, "肝内钙化灶"),
    ("胆囊结节", None, None, "胆囊结节"),
    ("肺结节", None, None, "肺结节"),
    ("钙", "2.31", "mmol/L", "钙(Ca)"),
    ("镁", "0.88", "mmol/L", "镁"),
    # 标准表外保留原名
    ("小而密低密度脂蛋白胆固醇", "1.1", "mmol/L", "小而密低密度脂蛋白胆固醇"),
    ("谷草/谷丙", "1.06", None, "谷草/谷丙"),
    ("前白蛋白", "231.6", "mg/L", "前白蛋白"),
])
def test_normalize_item_name(raw, result, unit, expected):
    n, _ = normalize_item_name(raw, result, unit)
    assert n == expected
