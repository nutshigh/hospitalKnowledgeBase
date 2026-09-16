"""版面层(2026-09-07 立项, Phase 0 骨架): PDF → 带坐标的视觉行。

目标(混合分层方案的 L0):
  PDF 每页按 dict 级 line 提取为 (text, x0, y0, x1, y1, page) 的视觉行,
  后续 Phase 1 在此之上做表区定位/列语义(文本块 x/y 对齐聚簇, 表格线辅助)。

原则: 本模块**只新增、不接线** —— process/提取器不改动; 语义正则不进本层。
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class VRow:
    """一个视觉文本行(同 y 上的一段连续文本, fitz line 粒度)。"""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int  # 1-based 页码


def extract_visual_rows(pdf_path: str) -> List[VRow]:
    """整份 PDF 的视觉行(按页内 line 遍历顺序返回)。

    line 文本 = 该行全部 span 文本拼接(span 间不加空格, 与既有行式管道的
    行粒度一致 —— span 边界空格由 fitz 文本流语义决定, 拼接后 strip)。
    """
    import fitz

    out: List[VRow] = []
    doc = fitz.open(pdf_path)
    try:
        for pno, page in enumerate(doc, start=1):
            d = page.get_text("dict")
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    t = "".join(s.get("text", "") for s in line.get("spans", []))
                    if not t.strip():
                        continue
                    x0, y0, x1, y1 = line.get("bbox") or (0, 0, 0, 0)
                    out.append(VRow(text=t.strip(), x0=x0, y0=y0,
                                    x1=x1, y1=y1, page=pno))
    finally:
        doc.close()
    return out


def visual_text(rows: List[VRow], page_mark: bool = True) -> str:
    """按 (page, y, x) 阅读序重排为文本(视觉排序), 供与 _extract_pdf_text
    (visual_sort=True) 对照/调试; 页面间可带 --- Page N --- 标记。"""
    texts: List[str] = []
    cur_page = None
    for r in sorted(rows, key=lambda r: (r.page, round(r.y0 / 10), r.x0)):
        if page_mark and r.page != cur_page:
            if cur_page is not None:
                texts.append("")
            texts.append(f"--- Page {r.page} ---")
            cur_page = r.page
        texts.append(r.text)
    return "\n".join(texts)


def coverage_ratio(pdf_path: str, rows: List[VRow]) -> float:
    """按页: 行文本拼接字符数 / 页原始文本字符数(均去空白)。
    Phase 0 骨架自检: 坐标行提取不丢文本。"""
    import fitz

    total_raw = 0
    total_join = 0
    doc = fitz.open(pdf_path)
    try:
        by_page: dict = {}
        for r in rows:
            by_page.setdefault(r.page, []).append(r)
        for pno in range(1, len(doc) + 1):
            raw = "".join(doc[pno - 1].get_text().split())
            joined = "".join("".join(r.text.split()) for r in by_page.get(pno, []))
            total_raw += len(raw)
            total_join += len(joined)
    finally:
        doc.close()
    return total_join / total_raw if total_raw else 1.0


# === Phase 1(2026-09-07): 表区检测 v1 ===
# 观测结论: fitz line 粒度 = 单元格; 同行多单元格 y 差 ≤0.5; 列 x0 高度规整。
# 表行 = 同 y 聚簇出 ≥2 个单元格; 字段标签行("体检编号: xxx", 含冒号)排除;
# 区域 = 连续表行段(y 间距 ≤ 3× 段内行距中位数)。
import statistics
from typing import List as _List, Tuple

_HEADER_CELL_WORDS = (
    "项目名称", "指标名称", "检查项目", "检验项目", "检查内容", "检查项目名称",
    "测定项目", "检查结果", "结果", "本次结果", "上次结果", "测定值",
    "单位", "参考范围", "参考值", "正常值", "提示", "标志", "异常标识", "临床意义",
)


@dataclass
class TableRegion:
    page: int
    y0: float
    y1: float
    x0: float
    x1: float
    rows: List[VRow]  # 区域内全部视觉行(单元格粒度, 原顺序)


def logical_lines(page_rows: List[VRow], tol: float = 10.0) -> List[List[VRow]]:
    """同 y 链式聚簇为逻辑行(容差 tol=10: 名称折行续行 y 差可达 9.1, 表行距 ≥11.4):
    表行单元格 y 差 ≤0.5 + 名称折行("名/值/折行名" 跨 ≤8pt)可并入同一逻辑行。"""
    out: List[List[VRow]] = []
    for r in sorted(page_rows, key=lambda r: (r.y0, r.x0)):
        if out and abs(r.y0 - out[-1][-1].y0) <= tol:
            out[-1].append(r)
        else:
            out.append([r])
    for cells in out:
        cells.sort(key=lambda c: c.x0)
    return out


def _logical_table_rows(page_rows: List[VRow]) -> List[Tuple[float, List[VRow]]]:
    """同 y 聚簇(容差 8)为逻辑行, 返回 [(y_mid, cells by x)]。"""
    return [(cells[0].y0, cells) for cells in logical_lines(page_rows)]


def detect_table_regions(pdf_path: str) -> List[TableRegion]:
    """页内表行段检测(规则 v1):
    - 表行: ≥2 个单元格 且 无"字段标签"(单元格含冒号且非单位/值形态);
    - 页眉/页脚(1 单元格长行 或 字段标签行)不算表行;
    - 连续表行 y 间距 ≤ 3×段内中位行距 → 同一区域; 大空隙断开。
    """
    import fitz

    regions: List[TableRegion] = []
    doc = fitz.open(pdf_path)
    try:
        for pno in range(1, len(doc) + 1):
            d = doc[pno - 1].get_text("dict")
            page_rows: List[VRow] = []
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    t = "".join(s.get("text", "") for s in line.get("spans", []))
                    if not t.strip():
                        continue
                    x0, y0, x1, y1 = line.get("bbox") or (0, 0, 0, 0)
                    page_rows.append(VRow(t.strip(), x0, y0, x1, y1, pno))
            logical = _logical_table_rows(page_rows)

            import re as _re

            _FIELD_CELL = _re.compile(
                r"^(体检号|体检编号|体检号|姓名|性别|年龄|证件号|登记号|档案号|条码|"
                r"手机|电话|打印日期|科室|医生|检查时间)[:：]")
            _JUNK_CELL = _re.compile(r"^(第|页/共\d+页|共\d+页|\d{1,3}|[-—]|页)$")

            def _table_row(cells: List[VRow]) -> bool:
                if len(cells) < 2:
                    return False
                texts = [c.text for c in cells]
                # 字段标签行(体检编号:xx / 姓名:xx 多 cell 页眉)
                if any(_FIELD_CELL.match(t) for t in texts):
                    return False
                # 图轴/历次对比行
                if any("历年对比" in t or "对比图" in t or "历次体检" in t for t in texts):
                    return False
                # 页脚/刻度行(第/页/共N页/纯数字 等标记 ≥ 半数)
                if sum(1 for t in texts if _JUNK_CELL.match(t)) * 2 >= len(texts):
                    return False
                return True

            # 表行段切分
            seg: List[Tuple[float, List[VRow]]] = []
            gaps: List[float] = []
            for y, cells in logical:
                if _table_row(cells):
                    seg.append((y, cells))
            # y 间距中位数
            ys = [y for y, _ in seg]
            mids = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
            med = statistics.median(mids) if mids else 0.0
            # 下限 90: 同页连续小节表(血脂块/生化块)间隙可达 50-60pt, 仍属同表区;
            # 正文/页间空隙(>200pt)才会断开
            gap_max = max(3.0 * med, 90.0) if med else 90.0
            # 重组段: 相邻间距 > gap_max 断开
            cur: List[Tuple[float, List[VRow]]] = []
            for k, item in enumerate(seg):
                if cur and ys[k] - ys[k - 1] > gap_max:
                    _flush(cur, regions, pno)
                    cur = []
                cur.append(item)
            _flush(cur, regions, pno)
    finally:
        doc.close()
    return regions


def _flush(seg: List[Tuple[float, List[VRow]]], regions: List[TableRegion], pno: int):
    if len(seg) < 2:
        return
    rows: List[VRow] = []
    for _, cells in seg:
        rows.extend(cells)
    y0 = min(c.y0 for c in rows)
    y1 = max(c.y1 for c in rows)
    x0 = min(c.x0 for c in rows)
    x1 = max(c.x1 for c in rows)
    regions.append(TableRegion(page=pno, y0=y0, y1=y1, x0=x0, x1=x1, rows=rows))


# === Phase 2(2026-09-07): 列语义 —— 表头词 → 列角色 + x 区间; 数据行按列组装 ===
# 设计: 表头行(≥2 个 cell 命中角色词)给出 (role → (x0,x1)); 数据行 cell 按 x0
# 归列(容差 3pt); 归不进任何列的窄 cell(如 华西 ↑ 列)作为"列外标记"。无表头的
# region(续表/华西上半块)继承"同页前一个表头 spec"(y 向前最近)。
_ROLE_WORDS = {
    "name": ("项目名称", "检验项目", "检查项目", "检查项目名称", "指标名称",
             "检查内容", "测定项目", "序号项目名称", "项目"),
    "result": ("检查结果", "结果", "本次结果", "测定值"),
    "prev": ("上次结果", "历史结果"),
    "ref": ("参考值", "参考范围", "正常值", "提示参考范围"),
    "unit": ("单位",),
    "flag": ("提示", "标志", "异常标识", "临床意义"),
}
_ROLE_FLAT = {w: role for role, ws in _ROLE_WORDS.items() for w in ws}
_COL_TOL = 3.0


class ColSpec:
    """表头行推断出的列角色 → x 区间。"""
    def __init__(self, page: int, y: float, spans: dict):
        self.page = page
        self.y = y
        self.spans = spans  # role -> (x0, x1)

    def role_of(self, x0: float) -> Optional[str]:
        for role, (cx0, cx1) in self.spans.items():
            if cx0 - _COL_TOL <= x0 <= cx1 + _COL_TOL:
                return role
        return None

    @property
    def ok(self) -> bool:
        return "name" in self.spans and "result" in self.spans


def cell_role(t: str) -> Optional[str]:
    """单元格文本 → 角色(最长词优先子串匹配; "英文缩写    检查结果" → result)。
    2026-09-16: 词间空白先归一("单 位"/"参 考 值" 陈镜霓白带表, 此前 unit/ref 角色
    识别为 None → 单位/参考列整列被丢弃, 组装行丢失 /HP 单位)。"""
    t = "".join((t or "").split())
    for w, role in sorted(_ROLE_FLAT.items(), key=lambda kv: -len(kv[0])):
        if w in t:
            return role
    return None


def header_roles(cells: List[VRow]) -> Optional[List[Optional[str]]]:
    """表头逻辑行 → 角色序列(按 x 序); 非表头行返回 None。
    判定: ≥2 个 cell 命中角色词且含 result 与 name/或列数 ≥3 且含 result。"""
    roles = [cell_role(c.text.strip()) for c in cells]
    words = [r for r in roles if r]
    if len(words) >= 2 and "result" in words and (len(cells) >= 3 or "name" in words):
        return roles
    return None
