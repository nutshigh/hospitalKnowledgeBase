"""指标提取回归护栏(2026-09-05): 规则提取器(table_extractor)改动必须通过本组断言。

样本 = 23:56 批 13 份中用户报修的报告 PDF(storage 中 8005 侧上传原文件)。
断言覆盖各报告的关键指标形态(参考范围/定性阳性/▲前缀/信号标志),防"修 A 坏 B"。
无 LLM、无 DB,纯规则提取。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.modules.report.service import (  # noqa: E402
    _compile_profile_re,
    _extract_pdf_text,
    _load_report_profiles,
    _locate_findings_sections,
)
from app.modules.report.table_extractor import (  # noqa: E402
    extract_abnormal_signals,
    extract_column_table_rows,
    extract_indicator_rows,
)

_BATCH = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例/各地汇总"
_GX = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例/广西体检报告测试"
_SAMPLE_ROOT = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例"

# storage hash 前缀 → 入库样本文件名(2026-09-05 样本已入库 体检报告样例/)
_TASK_FILE = {
    "545b56aa": "德宏州人民_H003_10.pdf",
    "39e15842": "滨州人民_H003_10.PDF",
    "1485f7de": "潮州第一_H003_10.pdf",
    "807b8db1": "福建第二_H003_10.PDF",
    "4d581bdd": "马鞍山人民_H003_10.pdf",
}


def _indicator_rows(pdf: str) -> list[dict]:
    text = _extract_pdf_text(pdf, hybrid=False)
    profile, _ = _load_report_profiles()
    comp = _compile_profile_re(profile(text))
    sec = _locate_findings_sections(
        text, extra_break_re=comp["extra_break_re"],
        extra_skip_re=comp["extra_skip_re"], extra_anchor_re=comp["extra_anchor_re"])
    t2 = text.replace(sec, "") if sec and len(sec) > 50 else text
    rows = extract_indicator_rows(t2)
    col = extract_column_table_rows(t2)
    signals = extract_abnormal_signals(pdf)
    all_items: list[dict] = list(rows) + list(col) + list(signals)
    merged: dict[tuple, dict] = {}
    for it in all_items:
        key = (it["item_name"], it["result"])
        cur = merged.get(key) or {}
        ref_ok = bool(it.get("ref_low") or it.get("ref_high"))
        prev_ref_ok = bool(cur.get("ref_low") or cur.get("ref_high"))
        sig_flag = it.get("signal_flag") or (2 if it.get("signal") == "arrow" else
                                             (1 if it.get("signal") else 0))
        flag = max(sig_flag, cur.get("signal_flag") or 0)
        if ref_ok and not prev_ref_ok:
            cur.update({k: it.get(k) for k in ("ref_low", "ref_high", "unit")})
        cur["item_name"], cur["result"] = key
        cur["signal_flag"] = flag
        merged[key] = cur
    return list(merged.values())


def _find(items, name_part):
    return [i for i in items if name_part in i["item_name"]]


@pytest.mark.skipif(not os.path.isdir(_BATCH), reason="样本目录不存在")
class TestIndicatorRegression:
    def _rows(self, task):
        pdf = os.path.join(_BATCH, _TASK_FILE[task])
        assert os.path.exists(pdf), f"样本 {task} 缺失"
        return _indicator_rows(pdf)

    def test_dehong_baso_ref_and_signal(self):
        items = self._rows("545b56aa")  # 德宏(毕建国)
        ba = _find(items, "嗜碱性粒细胞百分比(BA%)")
        ba2 = _find(items, "嗜碱性粒细胞数(BA#)")
        assert ba and any(i.get("ref_high") == "1" for i in ba), ba
        assert ba2 and any(i.get("ref_high") == "0.06" for i in ba2), ba2
        # 结论区 "≥100" 提示不得进指标(表格 403 为准)
        assert not any(i["item_name"].startswith("乙肝表面抗体") and i["result"] == "≥100"
                       for i in _find(items, "乙肝表面抗体"))

    def test_binzhou_flag_ref_and_ascii(self):
        items = self._rows("39e15842")  # 滨州(董延广)
        ua = _find(items, "尿酸")
        assert ua and any(i["result"] == "478" and i.get("ref_low") == "202.3" for i in ua), ua
        fpsa = _find(items, "FPSA/TPSA")
        assert fpsa and any(i["result"] == "0.37" for i in fpsa), fpsa
        assert any(i["item_name"] == "FPSA/TPSA" and i["result"] == "0.37" and i["signal_flag"]
                   for i in fpsa)

    def test_shantou_no_junk(self):
        items = self._rows("1485f7de")  # 汕头
        assert not _find(items, "审核日期")
        assert not _find(items, "边缘")

    def test_fujian2_hepatitis_qualitative(self):
        items = self._rows("807b8db1")  # 福建第二
        for nm in ("乙型肝炎表面抗原测定（定性）", "乙型肝炎表面抗体测定（定性）",
                   "乙型肝炎e抗体测定（定性）", "乙型肝炎核心抗体测定（定性）"):
            hits = _find(items, nm)
            assert hits and any(h["result"].startswith("阳性") for h in hits), nm
        assert not _find(items, "审核日期")
        assert not any("健康管理中心" in i["item_name"] for i in items)

    def test_maanshan_hpigg_and_pdw(self):
        items = self._rows("4d581bdd")  # 马鞍山(陈田)
        hp = _find(items, "幽门螺杆菌IgG 抗体(HP-IgG)")
        assert hp and any(i["result"] == "58.41" and i.get("ref_high") == "30.00" for i in hp), hp
        pdw = _find(items, "血小板分布宽度")
        assert pdw and any(i["result"] == "13.70" and i.get("ref_low") == "15.5" for i in pdw), pdw
        assert not any(i["item_name"] == "单位" for i in items)


# === 2026-09-05: 历史已适配样本(广西/北京)指标断言 —— 防共用规则改动回归 ===
class TestHistoricalIndicatorBaseline:
    def _rows(self, path):
        assert os.path.exists(path), f"样本缺失 {path}"
        return _indicator_rows(path)

    def test_liuzhou_blood_refs_not_shifted(self):
        """柳州: 值行 ref 不得错位(*单位行 + 前置 ref 逻辑曾把上一指标 ref 配给本行)。"""
        items = self._rows(os.path.join(_GX, "广西柳州市人民医院.pdf"))
        def ref_of(nm):
            hits = _find(items, nm)
            return [(i.get("ref_low"), i.get("ref_high")) for i in hits]
        assert ("4.3", "5.8") in ref_of("红细胞计数"), ref_of("红细胞计数")
        assert ("3.5", "9.5") in ref_of("白细胞计数"), ref_of("白细胞计数")
        assert ("1.8", "6.3") in ref_of("中性粒细胞绝对值"), ref_of("中性粒细胞绝对值")
        assert ("0.1", "0.6") in ref_of("单核细胞绝对值"), ref_of("单核细胞绝对值")

    def test_guangxi_people_hbsab_arrow_ref_flag(self):
        """广西人民: 值后参考行带 ↑ 前缀("↑0～10.0") = 异常信号, 不得判绿。"""
        items = self._rows(os.path.join(_GX, "广西壮族自治区人民医院.pdf"))
        hits = [i for i in items if "表面抗体" in i["item_name"] and i["result"] == "448.08"]
        assert hits and any(i.get("signal_flag") for i in hits), hits

    def test_chenmeisha_bmi_arrow_flag(self):
        """陈美杉(北京): "体重指数:26.38 (18-24) ↑" 箭头行不得因结论区排除而丢信号。"""
        items = self._rows(os.path.join(_SAMPLE_ROOT, "陈美杉_H003_10.pdf"))
        hits = [i for i in items if "体重指数" in i["item_name"] and i["result"] == "26.38"]
        assert hits and any(i.get("signal_flag") for i in hits), hits

    def test_guangxi_people_space_name_ctni(self):
        """广西人民: 名称内部空格("血清肌钙蛋白I 测定")不得漏提取, ref 0.0~0.03。"""
        items = self._rows(os.path.join(_GX, "广西壮族自治区人民医院.pdf"))
        hits = [i for i in items if "肌钙蛋白" in i["item_name"]]
        assert hits and any(i["result"] == "0.003" and i.get("ref_high") == "0.03" for i in hits), hits
