"""一次性脚本: USER6(H004) report 4 钦州中(图片型 PDF) OCR 重生成 conclusion_text + 提取落库。

背景(2026-09-03): 钦州中 PDF 15 页无文本层; 旧结论提取只 OCR 最后 3 页
(分科表格/DR/简介), conclusion_text 与总检结论无关。改为逐页 OCR 全文 →
锚点定位("本次体检结论及健康指导意见")→ 提取 7 条编号结论。
用法:
    .venv/bin/python scripts/rerun_h004_qinzhou_vlm.py
"""
import asyncio
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
from sqlalchemy import text

from app.core.database import get_session
from app.core.vlm_client import VLMClient
from app.modules.report.service import _extract_abnormalities_async, _locate_findings_sections, _store_abnormalities
from app.modules.interpretation.service import refresh_interpretation_counts

DB = "hospital_H004"
REPORT_ID = 4
PDF = "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/7f5ee68b429b476181d8634d63a0db8a.pdf"
# 8001(系统 paddle_venv)对扫描页 500; 8006(uv 实例)稳定
vlm_client = VLMClient("http://localhost:8006")


def _page_to_b64(doc, page_idx: int) -> str:
    pix = doc[page_idx].get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
    return base64.b64encode(pix.tobytes("png")).decode()


async def main():
    s = get_session(DB)
    try:
        doc = fitz.open(PDF)
        pages_b64 = [_page_to_b64(doc, p) for p in range(doc.page_count)]
        npages = doc.page_count
        doc.close()
        texts = []
        for i, img in enumerate(pages_b64):
            t0 = time.time()
            try:
                r = vlm_client.extract_from_image(img)
                raw = (r.get("raw_text") or "").strip()
            except Exception as e:
                raw = ""
                print(f"  OCR 页{i+1} 失败: {e}", flush=True)
            texts.append(raw)
            print(f"  OCR 页{i+1}/{npages} {len(raw)}字 {time.time()-t0:.0f}s", flush=True)
        full_text = "\n\n".join(texts)
        section = _locate_findings_sections(full_text) or ""
        print(f"结论段 {len(section)} 字", flush=True)
        if not section:
            return
        s.execute(text(f"UPDATE {DB}.report_info SET conclusion_text=:c WHERE id=:rid"),
                  {"c": section[:16000], "rid": REPORT_ID})
        s.commit()
        interp = s.execute(text(f"SELECT id FROM {DB}.report_interpretation WHERE report_id=:rid ORDER BY id DESC LIMIT 1"),
                           {"rid": REPORT_ID}).scalar()
        if not interp:
            print("无 interpretation", flush=True)
            return
        t0 = time.time()
        items = await _extract_abnormalities_async(section)
        s.execute(text(f"DELETE ij FROM {DB}.indicator_judgment ij "
                       f"JOIN {DB}.report_indicator ri ON ij.indicator_id=ri.id "
                       f"WHERE ij.interpretation_id=:iid AND ri.raw_text IS NOT NULL"),
                  {"iid": interp})
        s.execute(text(f"DELETE FROM {DB}.report_indicator WHERE raw_text IS NOT NULL "
                       f"AND id NOT IN (SELECT indicator_id FROM {DB}.indicator_judgment)"))
        s.commit()
        if items:
            _store_abnormalities(s, REPORT_ID, interp, items)
            s.commit()
            refresh_interpretation_counts(s, interp)
            s.commit()
            print(f"提取 {len(items)} 条, 用时 {time.time()-t0:.0f}s", flush=True)
        else:
            print("提取为空", flush=True)
    finally:
        s.close()


if __name__ == "__main__":
    asyncio.run(main())
