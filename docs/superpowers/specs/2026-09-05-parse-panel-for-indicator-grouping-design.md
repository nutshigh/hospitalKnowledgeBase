# 解析落库栏目(panel)修复「同名不同样本」误归组 设计

**日期**:2026-09-05
**状态**:Draft(已与用户对齐 §A/§B/§C/§D,待 review)
**前置**:
- 报告解读折叠展示:`docs/superpowers/specs/2026-09-05-report-indicator-collapse-design.md`
- 分类器:`backend/app/core/indicator_groups.py`(`classify`/`group_indicators`/`MODULE_ORDER`,`_RULES`)
- 工程约束:`AGENTS.md`

---

## 0. 背景与目标

### 背景(已知限制)
报告解读折叠把指标按 Excel 模块分组,纯靠指标名子串分类。但同一份报告里「尿试纸/尿沉渣」与「血液化验」存在同名裸指标(`葡萄糖`、`白细胞`、`红细胞`、`胆红素`等),解析落库时只存 `item_name`,章节上下文已丢,名字分类无法区分 → 尿试纸的 `葡萄糖=阴性` 被归进「空腹血糖」、`白细胞=阴性 个/HP` 归进「血常规」。影响仅分组归属,数值/单位/参考不串。

### 根因
- 报告 PDF 文本/图像里本有栏目结构(如文本标题「尿常规」「血常规（体检）,糖化血红蛋白」),但:
  - 解析 prompt 不要求输出栏目;
  - `report_indicator.category` 列(DDL 已建,一直未用)始终为 NULL。
- 展示端只能在读路径按 `item_name` 事后猜,注定分不出同名项。

### 目标
**只对新建的文本型 PDF 报告**:解析阶段让 MedGo LLM 给每条指标标出所属栏目并落库 `report_indicator.category`;读侧分组**优先用落库栏目,名字分类兜底**,从源头修掉裸名误归。存量报告与扫描/图片 PDF 不动(维持名字兜底,行为与现状一致)。

### 范围
- `backend/app/modules/report/service.py`:文本解析 prompt 加 `category` 输出 + 落库
- `backend/app/core/indicator_groups.py`:panel 合法性校验 + `group_indicators` 优先级(落库 category 优先)
- `backend/app/modules/interpretation/service.py`:`get_judgments_with_indicator_detail` 的 SELECT 带上 `i.category`
- 对应后端 pytest

### 范围外(YAGNI)
- **扫描/图片 PDF(PaddleOCR-VL 路径)**:OCR markdown 栏目标题不稳定,不改(名字兜底)
- **存量报告重解析回填**:不重跑历史 PDF(如需另立项)
- 前端:折叠 UI 已吃 `group`/`module_order`,零改动
- schema / DDL / 迁移:无(`report_indicator.category` 列已存在)

---

## 1. 解析侧(文本型 PDF):category 输出与落库

### 1.1 `backend/app/core/indicator_groups.py` 增补(供解析侧引用)
新增(模块级):
```python
# 可作落库/分组栏目的合法取值 = 能折叠的模块(有分类规则的子集),按 Excel 顺序
PANEL_HINTS: Tuple[str, ...] = tuple(m for m in MODULE_ORDER if m in _RULES)


def normalize_panel(raw: Optional[str]) -> Optional[str]:
    """清洗模型输出的栏目:去空白;命中 PANEL_HINTS 才返回,否则 None。"""
    if not raw:
        return None
    v = _norm(str(raw))
    return v if v in PANEL_HINTS else None
```
`_RULES` 为现有分类规则表(只有它能被 `classify` 命中、能作为折叠模块出现),因此**合法栏目集合 = 有规则的模块**,天然与展示端可折叠模块一致;不在 `PANEL_HINTS` 的栏目(如 尿核基质蛋白 NMP22、彩超结论)归 null → 平铺,与现状一致。

### 1.2 `report/service.py::_build_parse_prompt`(文本 PDF LLM 抽取)
JSON 示例的 `indicators` 项加 `"category": "该指标所属栏目,如 血常规"`,并加规则:

1. 每条指标必须给 `category`,取值**只能从下列允许栏目中选最接近的一项**,不能自创、不能加多余文字:
   `PANEL_HINTS` 全部名称(分号拼接内联进 prompt,约 40 个短名称,文本预算充足)
2. 栏目标题通常在指标表格上方,如「尿常规」「血常规（体检）,糖化血红蛋白」(一个表格标题含多栏目时,按各行实际归属拆标:血常规行→血常规,全血糖化血红蛋白测定→糖化血红蛋白)
3. 没有任何允许栏目能对上(如影像/结论类文本)则填 `null`
4. 参考范围/单位等其它字段规则不变

### 1.3 `report/service.py::process_task` 落库
`ReportIndicator(...)` 构造加 `category=normalize_panel(ind.get("category"))`,即**只落合法栏目,非法/缺失落 NULL**。

> `normalize_indicators` 去重键不含 category,保留首次出现;同一指标在不同栏目下同名不同值(尿糖 阴性 vs 血糖 5.7)本就因 result 不同而各自保留,落库后各自 category 正确。

---

## 2. 读侧:落库 category 优先,名字分类兜底

### 2.1 `indicator_groups.group_indicators(rows)` 优先级
每条 dict:
1. `stored = normalize_panel(row.get("category") or "")`;命中 → `group = stored`
2. 否则 → `group = classify(item_name)`(现状行为,不变)
`module_order` 排序逻辑不变。

### 2.2 两接口数据源补 `category`
- `GET /reports/{id}`:`get_report_detail` 的 indicator dict 已带 `i.category`(Task3 起就是 `"category": i.category`),自动生效,无需改 router。
- `GET /interpretations/{id}`:`get_judgments_with_indicator_detail`(interpretation/service.py)的 SQL `SELECT` 与返回 dict **加 `i.category`**,使解读列表也能吃到落库栏目。
  - schema:`IndicatorJudgmentSchema` 不加 `category`(响应只暴露 `group`),extra 字段 Pydantic 默认忽略,无需改。

---

## 3. 边界与兼容

- 存量报告:category 为 NULL → 走名字兜底,展示与改动前完全一致。
- 新建扫描/图片 PDF:category NULL → 同上。
- 文本型新报告:解析后 `report_indicator.category` ∈ `PANEL_HINTS`,读侧 `group` 直接取它。
- 模型偶尔乱标合法值(如把血常规行标成肝功能):**信任落库值**(用户已确认 panel 优先);名字兜底只在无 category 时启用。
- 全程无 DDL / 迁移 / 响应结构变化。

---

## 4. 测试

| 层 | 文件 | 用例 |
|------|------|------|
| 分类器 | `backend/tests/core/test_indicator_panel.py` | `normalize_panel`(命中/去空白/非法→None);`group_indicators` 带合法 `category` 覆盖名字分类(如 `葡萄糖`+`尿常规`→尿常规);非法 category 回落名字(如 category=`随意` + `葡萄糖`→空腹血糖);category 缺失行为不变 |
| 解析落库 | `backend/tests/test_parse_category_persist.py` | 仿 `test_report_parsed_name.py` sqlite 模式:mock 文本解析返回带 `category` 指标 → `ReportIndicator.category` 正确落库;非法 category → NULL;`_build_parse_prompt` 文本含 `category` 与至少一个允许栏目名 |
| 解读 join | `backend/tests/test_interp_join_category.py` | sqlite 建 report/interpretation 三表,mock `interp.id`,断言 `get_judgments_with_indicator_detail` 返回的每行 dict 带 `category`(来自 `report_indicator.category`) |

回归:既有折叠相关测试全绿(`test_indicator_groups.py` / `test_report_detail_grouping.py` / `test_interp_detail_grouping.py` 等)。

## 5. 验收

1. 新建一份文本型 PDF(北京医院格式):尿试纸 `葡萄糖`/`白细胞` 所在行 `category` 落为 `尿常规`,CBC `白细胞` 落 `血常规`,生化表 `葡萄糖` 落 `空腹血糖`;前端折叠归组正确。
2. 存量报告打开后分组表现与改动前一致。
3. 扫描/图片 PDF 报告行为不变(名字兜底)。
4. 后端 pytest 全绿。
