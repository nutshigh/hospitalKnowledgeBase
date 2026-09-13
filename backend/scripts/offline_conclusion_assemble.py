"""离线结论侧仿真(2026-09-08): 复刻 service.process_task 的"总结段 + 总检异常"链。

与生产同构:
  text = _extract_pdf_text(pdf)             # 全文(默认 hybrid=False 快验; --hybrid 走 OCR)
  profile = match_profile(text); compiled = compile_profile(profile)
  conclusion = _extract_conclusion_async(text, profile)   # 锚点切段优先(规则), 未命中回退 LLM
  items      = _extract_abnormalities_async(conclusion)   # LLM(8004) 提取异常条目
对比 DB(report 该 report 的 completed interpretation 下 raw 结论 judgments);
--sync: 更新 report_info.conclusion_text + 按 items 重建 raw 结论行/judgments(预览态,
       结论条目与端到端 backfill 同构 —— 先删后插)。
⚠ 若改 service 结论链逻辑需同步本脚本; 结果 ≠ 端到端的已知差异: LLM 随机性(与端到端同源
低温度, 基本一致); 不生成 summary_text/AI 总结(那是解读 agent 产物)。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql

from app.modules.report.service import (
    _compile_profile_re, _extract_abnormalities_async, _extract_conclusion_async,
    _extract_pdf_text, _load_report_profiles, _store_abnormalities,
)


def run(pdf_path: str, hybrid: bool = False):
    text = _extract_pdf_text(pdf_path, hybrid=hybrid)
    match_profile, _ = _load_report_profiles()
    profile = match_profile(text)
    if profile and profile.get("visual_sort"):
        # 2026-09-08: 莆田九十五等逐 cell dump 乱序模板 → 版面(y)排序文本再定位结论
        text = _extract_pdf_text(pdf_path, visual_sort=True)
    conclusion = asyncio.run(_extract_conclusion_async(text, profile))
    if not conclusion:
        return None, None
    items = asyncio.run(_extract_abnormalities_async(
        conclusion, weak_candidates=bool(profile.get("multi_findings"))))
    return conclusion, items


def main():
    pdf = sys.argv[1]
    db = "hospital_H004"
    if "--db" in sys.argv:
        db = "hospital_" + sys.argv[sys.argv.index("--db") + 1]
    hybrid = "--hybrid" in sys.argv
    sync = "--sync" in sys.argv
    text_only = "--text-only" in sys.argv
    report_id = None
    if "--report-id" in sys.argv:
        report_id = int(sys.argv[sys.argv.index("--report-id") + 1])
    conclusion, items = run(pdf, hybrid=hybrid)
    if not conclusion:
        print("结论段定位失败(conclusion=None)")
        return
    print(f"conclusion_text {len(conclusion)} 字; items {len(items) if items else 0}:")
    for it in (items or []):
        print("  -", it.get("item_name"), "|", (it.get("suggestion") or "")[:40], "| urgent",
              it.get("is_urgent"))
    if report_id is None:
        return
    conn = pymysql.connect(host="127.0.0.1", user="root", password="root",
                           db=db, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT id FROM report_interpretation WHERE report_id=%s AND status='completed' "
                "ORDER BY id DESC LIMIT 1", (report_id,))
    row = cur.fetchone()
    print("当前 completed interpretation:", row)
    if text_only and row:
        cur.execute("UPDATE report_info SET conclusion_text=%s WHERE id=%s", (conclusion, report_id))
        print("text-only 更新 conclusion_text 完成")
        conn.close()
        return
    if sync and row:
        iid = row[0]
        # 旧结论(raw 占位 judgments)快照(供 diff 报告: 本次 vs 上次提取产物)
        cur.execute("""SELECT ij.item_name, ij.suggestion FROM indicator_judgment ij
                       JOIN report_indicator ri ON ij.indicator_id = ri.id
                       WHERE ij.interpretation_id=%s AND ri.raw_text IS NOT NULL""", (iid,))
        prev_rows = [(r[0], r[1] or "") for r in cur.fetchall()]
        # 旧结论(raw 占位 judgments)清空 + 孤儿 raw 行清理
        cur.execute("""DELETE ij FROM indicator_judgment ij
                       JOIN report_indicator ri ON ij.indicator_id = ri.id
                       WHERE ij.interpretation_id=%s AND ri.raw_text IS NOT NULL""", (iid,))
        cur.execute("""DELETE FROM report_indicator
                       WHERE report_id=%s AND raw_text IS NOT NULL AND id NOT IN
                         (SELECT indicator_id FROM indicator_judgment)""", (report_id,))
        cur.execute("UPDATE report_info SET conclusion_text=%s WHERE id=%s",
                    (conclusion, report_id))
        if items:
            # 复用生产 backfill 落库函数(与端到端同构)
            s = None
            try:
                from app.core.database import get_session
                s = get_session(db)
                _store_abnormalities(s, report_id, iid, items)
                s.commit()
            finally:
                if s:
                    s.close()
        # 重算计数(指标+结论 judgments)
        cur.execute("""SELECT color_level, COUNT(*) FROM indicator_judgment
                       WHERE interpretation_id=%s GROUP BY color_level""", (iid,))
        counts = {c: n for c, n in cur.fetchall()}
        overall = "red" if counts.get("red") else ("yellow" if counts.get("yellow") else "green")
        cur.execute("UPDATE report_interpretation SET overall_level=%s, red_count=%s, "
                    "yellow_count=%s, green_count=%s WHERE id=%s",
                    (overall, counts.get("red", 0), counts.get("yellow", 0),
                     counts.get("green", 0), iid))
        print("sync 完成:", counts)
        # === 2026-09-09: diff 报告(提取产物前后对比 + 标题覆盖缺口) ===
        # 统一落到 artifacts/conclusion_diff/ 单文件夹, 避免散落; 仅诊断用不入库。
        _write_diff_report(report_id, iid, conclusion, prev_rows, items)
    elif report_id is not None:
        print("(无 completed interpretation, 未同步; 预览态需先建 interpretation)")
    conn.close()


def _write_diff_report(report_id, iid, conclusion, prev_rows, items):
    from datetime import datetime
    from pathlib import Path

    from app.modules.report.service import _parse_numbered_titles
    out_dir = Path(__file__).resolve().parents[1] / "artifacts" / "conclusion_diff"
    out_dir.mkdir(parents=True, exist_ok=True)
    cur_items = {r["item_name"]: (r["suggestion"] or "") for r in items}
    prev_items = dict(prev_rows)
    added = [n for n in cur_items if n not in prev_items]
    removed = [n for n in prev_items if n not in cur_items]
    sug_changed = [n for n in cur_items if n in prev_items
                   and (cur_items[n] != prev_items[n] or bool(cur_items[n]) != bool(prev_items[n]))]
    # 标题覆盖缺口: 报告方编号/★/【】标题全集 vs 本次提取(可能被指标跨线去重, 仅提示)
    titles = _parse_numbered_titles(conclusion)
    missing = [t for t in titles
               if not any(t == n or t in n or (n in t and len(n) + 3 > len(t)) for n in cur_items)]
    fn = out_dir / f"{datetime.now():%Y%m%d_%H%M%S}_report{report_id}.md"
    lines = [
        f"# 结论侧提取 diff report {report_id}(interp {iid})",
        f"- 时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- conclusion_text: {len(conclusion)} 字 | 本次提取 {len(cur_items)} 条 | 上次落库 {len(prev_items)} 条",
        "",
        "## 新增条目",
    ] + ([f"- {n}" + (f" | sug: {cur_items[n][:40]}" if cur_items[n] else "") for n in added] or ["(无)"])
    lines += ["", "## 移除条目"] + ([f"- {n}" for n in removed] or ["(无)"])
    lines += ["", "## suggestion 变化(同名)"] + (
        [f"- {n}\n  - 旧: {prev_items[n][:60] or '(空)'}\n  - 新: {cur_items[n][:60] or '(空)'}"
         for n in sug_changed] or ["(无)"])
    lines += ["", "## 报告方标题但本次结果缺失(可能被指标跨线去重/滤卡, 需人工核对)"] + (
        [f"- {t}" for t in missing] or ["(无)"])
    out_dir.joinpath(fn).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("diff 报告:", fn)


if __name__ == "__main__":
    main()
