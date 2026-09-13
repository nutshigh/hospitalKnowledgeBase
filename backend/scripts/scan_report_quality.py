"""报告结论链路质量扫描 —— 新模板/批量报告"会不会翻车"的快速体检。

无 LLM、纯确定性, 对每个 PDF:
  1. 提取文本(生产同款: 每页 --- Page N ---; 命中医院档案则按 visual 重排)
  2. 切段定位结论段; 3. 展示重排; 4. 确定性标题/小结条目解析
输出每份一行: 结论段字数/行数/脏行/标题数 + 可疑标记(⚠)。

可疑判定(任一命中即 ⚠):
  - 结论段定位失败(文本型但无锚点/过短)
  - 脏行残留(页眉/分检/签名/装饰)
  - 结论段 <100 字(文本型且全文较长) —— 疑似没切到真结论
  - 展示重排后行数异常(1 行?)或含未定义标记

用法:
  .venv/bin/python scripts/scan_report_quality.py [PDF|目录]...
  不带参数默认扫 ../体检报告样例/
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz

from app.modules.report.report_profiles import compile_profile, match_profile
from app.modules.report.service import (
    _extract_pdf_text,
    _locate_findings_sections,
    _parse_numbered_titles,
    _parse_summary_item_titles,
    _reflow_conclusion_lines,
)

DIRTY_WORDS = [
    "体检编号", "流水号", "姓名：", "性别：", "年龄：", "单位：", "体检号",
    "体 格 检 查", "检 验 报 告", "检验报告", "初检", "初审医生", "初审日期",
    "终审医生", "终审医师", "终审日期", "主检:",
    "咨询电话", "健康热线", "欢迎您", "温馨提示", "****", "请您仔细阅读体检报告",
]
DIRTY_RE = re.compile("|".join(re.escape(w) for w in DIRTY_WORDS))


def scan_pdf(path: Path) -> dict:
    doc = fitz.open(str(path))
    n_pages = doc.page_count
    total_chars = sum(len(p.get_text()) for p in doc)
    has_text = total_chars > 200
    if not has_text:
        doc.close()
        return dict(file=path.name, kind="image", text_chars=0, n_pages=n_pages,
                    flags=["图片型(需 OCR), 未扫描文本结论"])
    # 与生产同序: 默认文本 → profile → (visual)重取
    text = _extract_pdf_text(str(path))
    prof = compile_profile(match_profile(text))
    if prof.get("visual_sort"):
        text = _extract_pdf_text(str(path), visual_sort=True)
    sec = _locate_findings_sections(
        text, extra_break_re=prof["extra_break_re"], extra_skip_re=prof["extra_skip_re"],
        extra_anchor_re=prof["extra_anchor_re"])
    flags = []
    if not sec or len(sec) <= 50:
        doc.close()
        return dict(file=path.name, kind="text", text_chars=total_chars, n_pages=n_pages,
                    section_len=0, flags=["⚠ 结论段定位失败"])
    polished = _reflow_conclusion_lines(sec)
    lines = [ln.strip() for ln in polished.splitlines() if ln.strip()]
    dirty = [ln[:44] for ln in lines if DIRTY_RE.search(ln)]
    if dirty:
        flags.append(f"⚠ 脏行 {len(dirty)}: {dirty[0]!r}")
    if len(polished) < 100 and n_pages > 2:
        flags.append("⚠ 结论段过短(<100字)")
    if len(lines) <= 1:
        flags.append("⚠ 重排后仅 1 行")
    titles = _parse_numbered_titles(polished)
    summary = _parse_summary_item_titles(polished)
    if not titles and not summary and not flags:
        flags.append("⚠ 未解析出任何确定性条目(疑似定位到非结论内容)")
    doc.close()
    return dict(file=path.name, kind="text", text_chars=total_chars, n_pages=n_pages,
                section_len=len(polished), n_lines=len(lines),
                n_titles=len(titles) + len(summary),
                flags=flags if flags else ["OK"])


def main(argv) -> int:
    targets = [Path(a) for a in argv] or [Path("../体检报告样例")]
    pdfs = []
    for t in targets:
        if t.is_dir():
            pdfs += sorted(p for p in t.rglob("*") if p.suffix.lower() in (".pdf",))
        elif t.suffix.lower() == ".pdf":
            pdfs.append(t)
    print(f"{'文件':<46} {'类型':<5} {'页':>3} {'字符':>7} {'结论段':>6} {'行':>4} {'条目':>4}  判定")
    print("-" * 110)
    n_warn = 0
    for p in pdfs:
        try:
            r = scan_pdf(p)
        except Exception as e:
            print(f"{p.name:<46} 异常: {e}")
            n_warn += 1
            continue
        warn = any(f.startswith("⚠") for f in r["flags"])
        n_warn += warn
        tag = "⚠" if warn else "OK"
        print(f"{r['file']:<46} {r['kind']:<5} {r['n_pages']:>3} {r['text_chars']:>7} "
              f"{r.get('section_len', 0):>6} {r.get('n_lines', 0):>4} {r.get('n_titles', 0):>4}  "
              f"{'; '.join(r['flags'])}")
    print(f"\n共 {len(pdfs)} 份, 可疑 {n_warn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
