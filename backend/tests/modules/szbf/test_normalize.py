from datetime import date

from app.modules.szbf.normalize import (
    compute_age,
    is_imaging_item,
    normalize_deviation,
    normalize_gender,
    normalize_item_name,
    parse_date,
    parse_reference_range,
    synthetic_anchor,
)


def test_gender():
    assert normalize_gender("1") == "男"
    assert normalize_gender("2") == "女"
    assert normalize_gender("男") == "男"
    assert normalize_gender(None) is None


def test_item_name_strips_gender_suffix():
    assert normalize_item_name("丙氨酸氨基转移酶(谷丙酶)女") == "丙氨酸氨基转移酶(谷丙酶)"
    assert normalize_item_name("血红蛋白(HGB)男") == "血红蛋白(HGB)"
    assert normalize_item_name("红细胞数(RBC)女") == "红细胞数(RBC)"
    assert normalize_item_name(" 总胆固醇(CHOL) ") == "总胆固醇(CHOL)"


def test_reference_range():
    assert parse_reference_range("0.29-1.70mmol/L") == ("0.29", "1.70")
    assert parse_reference_range("≤5ng/ml") == (None, "5")
    assert parse_reference_range(">0.25") == ("0.25", None)
    assert parse_reference_range("阴性") == (None, None)


def test_age():
    assert compute_age(date(1990, 10, 30), date(2026, 10, 29)) == 35
    assert compute_age(date(1990, 10, 30), date(2026, 10, 30)) == 36
    assert compute_age(None, date(2026, 1, 1)) is None


def test_synthetic_anchor_stable():
    a = synthetic_anchor("778537160239349760")
    b = synthetic_anchor("778537160239349760")
    assert a == b
    assert len(a) == 16 and a.startswith("z")


def test_deviation_and_imaging():
    assert normalize_deviation("偏高") == "偏高"
    assert normalize_deviation("↓") == "偏低"
    assert normalize_deviation("异常") == "异常"
    assert is_imaging_item("腹部")
    assert not is_imaging_item("总胆固醇(CHOL)")
