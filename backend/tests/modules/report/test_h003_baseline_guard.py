"""H003 报告级三件套基线护栏(2026-09-12, DB 验收快照口径)。

基线 = **用户验收过的 DB 结果快照**(`scripts/gen_h003_baseline.py` 从 DB 导出)。
测试直接读 DB 当前产物与基线比对(三件套):
  ① 指标黄红名单(名称+色级, 双向)
  ② conclusion_text 归一化哈希
  ③ 总检异常条目名单(名称+色级, 双向)
报告重跑后若与验收态不同即红 → 人工审 diff(确认合理后重跑 gen 更新基线)。

更新流程: 重跑 gen 脚本(从 DB 导出) → 人工审 git diff → 提交。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

import pymysql  # noqa: E402

DB = "hospital_H003"
BASELINE = json.loads(
    (BACKEND / "tests" / "modules" / "report" / "baselines" / "h003_baseline.json")
    .read_text(encoding="utf-8"))


def norm(n: str) -> str:
    return (n or "").replace(" ", "").replace("\u3000", "")


def norm_text(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


@pytest.fixture(scope="module")
def conn():
    try:
        c = pymysql.connect(host="127.0.0.1", user="root", password="root",
                            db=DB, charset="utf8mb4")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"DB 不可用: {e}")
    yield c
    c.close()


def _latest_iid(cur, rid):
    cur.execute("SELECT id FROM report_interpretation WHERE report_id=%s "
                "AND status='completed' ORDER BY id DESC LIMIT 1", (rid,))
    r = cur.fetchone()
    return r[0] if r else None


def _actual(cur, rid):
    iid = _latest_iid(cur, rid)
    if not iid:
        return None
    cur.execute("""SELECT ij.item_name, ij.color_level FROM indicator_judgment ij
        WHERE ij.interpretation_id=%s AND ij.source='conclusion'""", (iid,))
    conclusions = sorted((norm(x[0]), x[1]) for x in cur.fetchall())
    cur.execute("""SELECT ij.item_name, ij.color_level FROM indicator_judgment ij
        WHERE ij.interpretation_id=%s AND ij.source='indicator'
          AND ij.color_level IN ('yellow','red')""", (iid,))
    indicators = sorted((norm(x[0]), x[1]) for x in cur.fetchall())
    cur.execute("SELECT conclusion_text FROM report_info WHERE id=%s", (rid,))
    conc = cur.fetchone()[0] or ""
    return {
        "conclusion_len": len(conc),
        "conclusion_hash": hashlib.sha1(norm_text(conc).encode("utf-8")).hexdigest()[:16],
        "indicators": indicators,
        "conclusions": conclusions,
    }


@pytest.mark.parametrize("rid", sorted(BASELINE.keys(), key=int))
def test_indicator_guard(rid, conn):
    """① 指标黄红名单双向断言。"""
    entry = BASELINE[rid]
    cur = conn.cursor()
    act = _actual(cur, rid)
    assert act, f"H003-{rid} {entry['name']} 无 completed 解读"
    got, want = act["indicators"], [tuple(x) for x in entry["indicators"]]
    extra = [n for n, c in got if (n, c) not in want]
    missing = [n for n, c in want if (n, c) not in got]
    assert not extra and not missing, (
        f"H003-{rid} {entry['name']} 指标黄红与验收基线漂移\n  多: {extra}\n  少: {missing}")


@pytest.mark.parametrize("rid", sorted(BASELINE.keys(), key=int))
def test_conclusion_text_guard(rid, conn):
    """② conclusion_text 归一化哈希断言。"""
    entry = BASELINE[rid]
    cur = conn.cursor()
    act = _actual(cur, rid)
    assert act, f"H003-{rid} {entry['name']} 无 completed 解读"
    assert act["conclusion_hash"] == entry["conclusion_hash"], (
        f"H003-{rid} {entry['name']} 总结段变化(长度 {act['conclusion_len']} "
        f"vs 基线 {entry['conclusion_len']})")


@pytest.mark.parametrize("rid", sorted(BASELINE.keys(), key=int))
def test_conclusion_items_guard(rid, conn):
    """③ 总检异常条目名单双向断言。"""
    entry = BASELINE[rid]
    cur = conn.cursor()
    act = _actual(cur, rid)
    assert act, f"H003-{rid} {entry['name']} 无 completed 解读"
    got, want = act["conclusions"], [tuple(x) for x in entry["conclusions"]]
    extra = [n for n, c in got if (n, c) not in want]
    missing = [n for n, c in want if (n, c) not in got]
    assert not extra and not missing, (
        f"H003-{rid} {entry['name']} 总检异常与验收基线漂移\n  多: {extra}\n  少: {missing}")
