# 报告解读后指标折叠展示(按体检采集模板模块)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户门户报告详情页解读完成后,指标不再全部平铺,而是按 Excel row2 模块默认全折叠展示;归不了模块的指标平铺在折叠区之后。

**Architecture:** 后端在 `GET /reports/{id}`、`GET /interpretations/{id}` 两个读接口为每条 indicator 注入 `group`(所属 Excel 模块名,`None` = 平铺),解读详情再给顶层 `module_order`(按 Excel 序的、本报告实际出现的模块名)。前端 `ReportDetailPage.tsx` 拿 `group`+`module_order` 分组,antd Collapse 默认全折叠渲染,模块标题行显示「模块名 N项 + 红/黄计数徽标」,展开显示组内指标(红黄绿优先)。

**Tech Stack:** Python 3.10 + FastAPI + Pydantic(sqlalchemy ORM,现有 repo 约定),pytest,前端 React 18 + antd v5 (user-portal),类型检查 `tsc`。

## Global Constraints

- 运行后端测试一律用 `cd backend && .venv/bin/python -m pytest <path> -q`(勿用系统 python)。
- 前端构建验证:`cd frontend/packages/user-portal && npm run build`(`tsc && vite build`,仓库无前端单测基建,以 tsc + 手工验收为准)。
- 不加 DB 列/迁移:`report_indicator.category` 等既有列不动,本特性全部在读路径计算。
- 模块名/顺序以 `data/附件2-体检信息采集模板_v1.20_最新.xlsx`「体检数据采集模板」sheet row2 为准(70 个模块,已在 Task1/2 固化)。
- 接口字段向后兼容:`group: Optional[str] = None`、`module_order: list[str] = []`,缺省旧客户端不报错。
- 代码不加注释、不加 emoji;沿用仓库风格(中文 commit message)。

---

### Task 1: 指标→模块分类器(纯函数)+ 单测

**Files:**
- Create: `backend/app/core/indicator_groups.py`
- Test: `backend/tests/core/test_indicator_groups.py`

**Interfaces:**
- Produces: `indicator_groups.MODULE_ORDER: list[str]`、`indicator_groups.classify(item_name: str) -> Optional[str]`、`indicator_groups.group_indicators(rows: list[dict]) -> tuple[list[dict], list[str]]`。Task3/4 依赖这三者。

- [ ] **Step 1: 写失败测试** `backend/tests/core/test_indicator_groups.py`

```python
from app.core.indicator_groups import classify, group_indicators


def test_classify_blood_routine_members():
    assert classify("白细胞") == "血常规"
    assert classify("血红蛋白(HGB)") == "血常规"
    assert classify("平均红细胞体积") == "血常规"
    assert classify("血小板压积(PCT)") == "血常规"
    assert classify("全血糖化血红蛋白测定") == "糖化血红蛋白"


def test_classify_urine_not_blood_routine():
    assert classify("尿白细胞（镜检）") == "尿常规"
    assert classify("尿白细胞(LEU)阴性") == "尿常规"
    assert classify("红细胞(高倍视野)") == "尿常规"
    assert classify("尿液颜色") == "尿常规"
    assert classify("尿酸碱度") == "尿常规"      # 含"尿酸"子串但不能进肾功能
    assert classify("尿微量白蛋白浓度") == "尿常规"
    assert classify("胆红素") == "尿常规"        # 尿试纸裸项(无总/直接前缀)
    assert classify("酮体") == "尿常规"
    assert classify("蛋白质") == "尿常规"
    assert classify("粘液丝") == "尿常规"
    assert classify("透明管型") == "尿常规"
    assert classify("比重") == "尿常规"
    assert classify("真菌") == "尿常规"


def test_classify_renal_not_urine():
    assert classify("尿酸(UA)") == "肾功能"
    assert classify("尿素") == "肾功能"
    assert classify("血尿酸") == "肾功能"
    assert classify("肌酐(酶法)") == "肾功能"
    assert classify("胱抑素C") == "肾功能"


def test_classify_liver_not_urine():
    assert classify("总胆红素(TBIL)") == "肝功能"
    assert classify("直接胆红素") == "肝功能"
    assert classify("血清间接胆红素(计算值)") == "肝功能"
    assert classify("谷草转氨酶") == "肝功能"
    assert classify("血清天门冬氨酸氨基转移酶") == "肝功能"
    assert classify("白蛋白/球蛋白(A/G)") == "肝功能"
    assert classify("总胆汁酸") == "肝功能"
    assert classify("总胆红质") == "肝功能"
    assert classify("直接胆红质") == "肝功能"
    assert classify("胆碱脂酶") == "肝功能"
    assert classify("丙氨酸氨基转移酶") == "肝功能"
    assert classify("白/球比值(A/G)") == "肝功能"


def test_classify_lipid_and_sugar():
    assert classify("总胆固醇(CHOL)") == "血脂"
    assert classify("甘油三酯") == "血脂"
    assert classify("小而密低密度脂蛋白胆固醇") == "血脂"
    assert classify("载脂蛋白B") == "血脂"
    assert classify("空腹血糖") == "空腹血糖"
    assert classify("葡萄糖") == "空腹血糖"


def test_classify_thyroid_not_surgical():
    assert classify("游离甲状腺素(FT4)测定") == "甲状腺功能"
    assert classify("促甲状腺激素(TSH)测定") == "甲状腺功能"
    assert classify("甲状腺素(T4)") == "甲状腺功能"
    assert classify("游离三碘甲状原氨酸") == "甲状腺功能"


def test_classify_physical_and_ent_oral():
    assert classify("裸眼视力右") == "眼科"
    assert classify("右眼眼压") == "眼科"
    assert classify("扁桃体") == "耳鼻喉科"
    assert classify("鼓膜") == "耳鼻喉科"
    assert classify("牙体") == "口腔科"
    assert classify("龋齿") == "口腔科"
    assert classify("淋巴结") == "外科"
    assert classify("腰臀比") == "身高体重血压"
    assert classify("杂音") == "内科"


def test_classify_tumor_markers_and_others():
    assert classify("甲胎蛋白(AFP)定量") == "甲胎蛋白(AFP)定量"
    assert classify("癌胚抗原(CEA)定量") == "癌胚抗原(CEA)定量"
    assert classify("癌抗原CA19-9") == "CA-199"
    assert classify("癌抗原CA125") == "CA125"
    assert classify("糖原蛋白125") == "CA125"
    assert classify("游离前列腺特异性抗原") == "前列腺特异性抗原"
    assert classify("乙肝表面抗体(HBsAb)") == "乙肝两对半"
    assert classify("血清同型半胱氨酸") == "同型半胱氨酸"
    assert classify("胃部幽门螺杆菌检测") == "胃部幽门螺杆菌检测"
    assert classify("心肌肌钙蛋白I") == "心肌酶谱"
    assert classify("乳酸脱氢酶") == "心肌酶谱"
    assert classify("钾(K)") == "电解质"
    assert classify("钠(Na)") == "电解质"
    assert classify("血沉") == "免疫检测"
    assert classify("C反应蛋白") == "免疫检测"
    assert classify("免疫球蛋白IgG") == "免疫检测"
    assert classify("T淋巴细胞亚群CD3") == "免疫检测"


def test_classify_unmatched_and_normalization():
    assert classify("肿瘤特异生长因子") is None
    assert classify("体检号") is None
    assert classify("咨询电话") is None
    assert classify("    白细胞  ") == "血常规"   # 首尾空白
    assert classify("白细胞 ") == "血常规"        # 尾部半角空格
    assert classify("裸眼视力 右") == "眼科"      # 内部空格(全角/半角混合场景)


def test_group_indicators_order_and_group():
    rows, module_order = group_indicators([
        {"item_name": "甲胎蛋白(AFP)定量"},
        {"item_name": "白细胞"},
        {"item_name": "血红蛋白"},
        {"item_name": "促甲状腺激素(TSH)测定"},
        {"item_name": "肿瘤特异生长因子"},
    ])
    # Excel 顺序:血常规(103) < 甲状腺功能(286) < 甲胎蛋白(AFP)定量(325)
    assert module_order == ["血常规", "甲状腺功能", "甲胎蛋白(AFP)定量"]
    by_name = {r["item_name"]: r["group"] for r in rows}
    assert by_name["白细胞"] == "血常规"
    assert by_name["血红蛋白"] == "血常规"
    assert by_name["促甲状腺激素(TSH)测定"] == "甲状腺功能"
    assert by_name["甲胎蛋白(AFP)定量"] == "甲胎蛋白(AFP)定量"
    assert by_name["肿瘤特异生长因子"] is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_indicator_groups.py -q`
Expected: FAIL(`ModuleNotFoundError: No module named 'app.core.indicator_groups'`)

- [ ] **Step 3: 实现 `backend/app/core/indicator_groups.py`**

```python
import re
from typing import Optional, Dict, List, Tuple


# 顺序 = Excel「体检数据采集模板」row2 模块从上到下(70 个),由 scripts/gen_indicator_modules.py 抽取固化。
MODULE_ORDER: List[str] = [
    "基本信息", "身高体重血压", "内科", "外科", "眼科", "耳鼻喉科", "口腔科", "皮肤科",
    "血常规", "尿常规", "尿微量蛋白（UMA)", "尿蛋白/尿肌酐比值", "大便常规", "肝功能",
    "空腹血糖", "糖化血红蛋白", "血脂", "肾功能", "心肌酶谱", "电解质", "甲状腺功能",
    "前列腺特异性抗原", "乙肝两对半", "同型半胱氨酸", "EB病毒抗体", "甲胎蛋白(AFP)定量",
    "癌胚抗原(CEA)定量", "CA-199", "CA724", "膀胱癌尿FISH测定", "CA125", "CA153",
    "鳞状细胞癌相关抗原 (SCC)", "神经元特异性烯醇化酶", "细胞角蛋白19片段",
    "胃部幽门螺杆菌检测", "妇检", "白带常规", "人乳头瘤病毒分型检测", "液基细胞学检查（女）",
    "心电图", "胸部X片", "胸部CT", "彩超(心脏)", "彩超(甲状腺)", "彩超(颈动脉)",
    "彩超(腹部+男泌尿系)", "彩超(子宫附件)", "彩超(乳腺)", "彩超(腹部+女泌尿系)",
    "彩色经颅多普勒", "骨密度检测", "无创动脉硬化检测", "胃肠镜", "免疫检测", "OCT",
    "肌电图", "冠脉CT", "心率变异检测", "心脏功能、心肌损伤检测", "血栓检测", "凝血功能检查",
    "肺功能检查", "血栓风险评估", "脑组织供氧评估", "头颅CT/核磁", "过敏原检测", "喉镜检查",
    "综述建议", "其他",
]


# include/exclude 均为子串匹配;按 MODULE_ORDER 顺序取第一个命中模块。
# exclude 用于拦截跨模块歧义(如血常规"白细胞"不吃尿/粪带"白细胞"的项)。
_RULES: Dict[str, Tuple[List[str], List[str]]] = {
    "身高体重血压": (["身高", "体重指数", "体质指数", "体重", "腰围", "臀围", "腰臀比", "收缩压", "舒张压", "血压", "脉搏"], []),
    "内科": (["心音", "心律", "心率", "心包摩擦", "呼吸音", "胸廓", "腹部压痛", "腹部包块", "肠鸣", "肝区", "肾区", "肋下", "心脏杂音", "杂音", "肺部", "肺气肿", "肝脏", "脾脏", "腹部", "双肺"], ["彩超", "超声", "CT", "核磁", "胸片", "X线", "心电图", "胃镜", "肠镜"]),
    "外科": (["脊柱", "四肢关节", "关节", "四肢", "淋巴结", "甲状腺结节", "乳房", "乳腺", "外生殖器", "肛门", "疝", "痔", "颈部", "腋下"], ["彩超", "超声", "CT", "核磁", "颌"]),
    "眼科": (["视力", "裸眼", "矫正", "眼压", "眼底", "视网膜", "角膜", "晶状体", "晶体", "玻璃体", "虹膜", "瞳孔", "眼睑", "结膜", "巩膜", "前房", "眼球", "外眼", "辨色", "色觉", "屈光", "眼疾", "眼科"], []),
    "耳鼻喉科": (["鼓膜", "听力", "嗅觉", "耵聍", "扁桃体", "声带", "鼻前庭", "鼻腔", "鼻中隔", "中鼻道", "外鼻", "咽部", "软腭", "咽喉", "喉镜", "外耳", "耳部", "鼻部", "喉部", "咽", "耳", "鼻", "喉"], []),
    "口腔科": (["牙", "牙龈", "龋", "口腔", "颞颌", "颞下颌", "涎腺", "腮腺", "舌", "缺齿", "牙周", "口吃", "齿槽"], []),
    "皮肤科": (["皮肤", "皮疹", "色素痣", "湿疹", "银屑"], []),
    "血常规": (["白细胞", "红细胞", "血红蛋白", "血小板", "红细胞压积", "红细胞比积", "平均红细胞", "平均血红蛋白", "有核红细胞", "红细胞分布宽度", "红细胞体积分布宽度", "大血小板", "大型血小板", "中性粒", "淋巴细胞", "单核细胞", "嗜酸", "嗜碱", "幼稚"], ["尿", "镜检", "粪", "便", "白带", "分泌物", "高倍视野", "低倍视野", "糖化", "脑脊液", "胸腹水", "T淋巴", "CD3", "CD4", "CD8"]),
    "尿常规": (["尿蛋白", "尿葡萄糖", "尿糖", "尿酮", "尿潜血", "尿隐血", "尿胆", "尿白细胞", "尿红细胞", "尿比重", "尿液", "尿色", "尿维生素", "微量白蛋白", "亚硝酸盐", "酮体", "蛋白质", "潜血", "胆红素", "酸碱度", "电导率", "管型", "结晶", "镜检", "粘液丝", "上皮细胞", "脓细胞", "白细胞团", "高倍视野", "低倍视野", "细菌", "真菌", "酵母", "吞噬细胞", "维生素C", "白细胞酯酶", "比重"], ["粪", "大便", "总胆", "直接胆", "间接胆", "结合胆", "非结合胆"]),
    "大便常规": (["粪", "便潜血", "隐血", "虫卵", "脂肪球", "脂肪滴", "未消化食物", "大便"], []),
    "肝功能": (["胆红素", "胆红质", "转氨酶", "谷草", "谷丙", "谷氨酰", "碱性磷酸酶", "总蛋白", "白蛋白", "球蛋白", "白球", "前白蛋白", "胆碱", "总胆汁酸", "天冬氨酸", "天门冬", "丙氨酸", "白/球"], ["免疫"]),
    "空腹血糖": (["空腹血糖", "空腹葡萄糖", "血糖", "葡萄糖"], []),
    "糖化血红蛋白": (["糖化"], []),
    "血脂": (["胆固醇", "甘油三酯", "甘油三脂", "高密度脂蛋白", "低密度脂蛋白", "载脂蛋白", "脂蛋白a", "脂蛋白(a)", "小而密", "非高密度"], []),
    "肾功能": (["尿素", "肌酐", "尿酸", "胱抑素", "肾小球滤过"], ["碱度", "盐", "呼气"]),
    "心肌酶谱": (["肌酸激酶", "乳酸脱氢酶", "羟丁酸", "肌钙蛋白", "肌红蛋白", "CK-MB", "CK同工酶"], []),
    "电解质": (["钾", "钠", "氯", "钙", "镁", "磷", "二氧化碳", "阴离子间隙", "渗透压"], []),
    "甲状腺功能": (["甲状腺素", "甲状腺原氨酸", "原氨酸", "促甲状腺", "游离T3", "游离T4", "FT3", "FT4", "摄取率", "TSH", "T3", "T4", "抗甲状腺", "过氧化物酶抗体"], []),
    "前列腺特异性抗原": (["前列腺特异", "TPSA", "FPSA", "PSA"], []),
    "乙肝两对半": (["乙肝", "HBsAg", "HBsAb", "HBeAg", "HBeAb", "HBcAb"], []),
    "同型半胱氨酸": (["同型半胱氨酸", "半胱氨酸", "Hcy"], []),
    "EB病毒抗体": (["EB病毒", "EBV"], []),
    "甲胎蛋白(AFP)定量": (["甲胎蛋白", "AFP"], []),
    "癌胚抗原(CEA)定量": (["癌胚抗原", "CEA"], []),
    "CA-199": (["CA19-9", "CA199", "癌抗原19", "19-9"], []),
    "CA724": (["CA724", "癌抗原72", "72-4"], []),
    "CA125": (["CA125", "癌抗原125", "糖原蛋白125", "糖类抗原125"], []),
    "CA153": (["CA153", "癌抗原153", "糖原蛋白153", "糖类抗原153"], []),
    "鳞状细胞癌相关抗原 (SCC)": (["鳞状细胞癌", "SCC"], []),
    "神经元特异性烯醇化酶": (["神经元特异性烯醇化酶", "NSE"], []),
    "细胞角蛋白19片段": (["细胞角蛋白"], []),
    "胃部幽门螺杆菌检测": (["幽门螺杆菌", "幽门螺旋杆菌", "螺杆菌"], []),
    "妇检": (["外阴", "阴道", "宫颈", "子宫", "附件", "盆腔", "卵巢", "妇检", "分泌物"], []),
    "白带常规": (["白带", "清洁度", "滴虫", "霉菌"], []),
    "人乳头瘤病毒分型检测": (["人乳头瘤病毒", "HPV"], []),
    "液基细胞学检查（女）": (["液基", "TCT", "宫颈刮片"], []),
    "免疫检测": (["免疫球蛋白", "补体", "类风湿", "C反应蛋白", "血沉", "抗核抗体", "抗链", "T淋巴细胞", "CD3", "CD4", "CD8"], []),
}


def _norm(name: str) -> str:
    return re.sub(r"[\s\u3000]+", "", name)


def classify(item_name: str) -> Optional[str]:
    if not item_name:
        return None
    name = _norm(item_name)
    for module in MODULE_ORDER:
        rule = _RULES.get(module)
        if rule is None:
            continue
        includes, excludes = rule
        if not any(tok in name for tok in includes):
            continue
        if any(tok in name for tok in excludes):
            continue
        return module
    return None


def group_indicators(rows: List[dict]) -> Tuple[List[dict], List[str]]:
    present: List[str] = []
    for row in rows:
        g = classify(str(row.get("item_name") or ""))
        row["group"] = g
        if g and g not in present:
            present.append(g)
    order = {m: i for i, m in enumerate(MODULE_ORDER)}
    present.sort(key=lambda m: order.get(m, len(MODULE_ORDER)))
    return rows, present
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_indicator_groups.py -q`
Expected: PASS(全部测试绿)

> 提示:若个别断言因数据模型认知偏差失败(如某 token 意外命中),以「真实报告中该指标最常出现的语义」为准调整 include/exclude,并在 Task2 覆盖率扫描里复核,不得为过测试而削弱规则。

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/indicator_groups.py backend/tests/core/test_indicator_groups.py
git commit -m "feat: 指标→Excel模块分类器 indicator_groups"
```

---

### Task 2: Excel 模块序快照 + 抽取脚本 + 漂移测试

**Files:**
- Create: `scripts/gen_indicator_modules.py`
- Create: `backend/tests/fixtures/excel_row2_modules.json`
- Test: `backend/tests/core/test_excel_module_order.py`

**Interfaces:**
- Produces: 快照 JSON(70 个模块名数组,等价于 Task1 `MODULE_ORDER`);供 Excel 升版时重生成 diff。运行期零依赖 openpyxl(仅系统 python 手工跑脚本)。

- [ ] **Step 1: 建 fixtures 目录并写失败测试**

```bash
mkdir -p backend/tests/fixtures
```

Create: `backend/tests/core/test_excel_module_order.py`

```python
import json
from pathlib import Path

from app.core.indicator_groups import MODULE_ORDER

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "excel_row2_modules.json"


def test_module_order_matches_excel_row2_snapshot():
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert MODULE_ORDER == snapshot
    assert len(snapshot) == 70
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_excel_module_order.py -q`
Expected: FAIL(`FileNotFoundError`)

- [ ] **Step 3: 创建快照 fixture `backend/tests/fixtures/excel_row2_modules.json`**

```json
["基本信息", "身高体重血压", "内科", "外科", "眼科", "耳鼻喉科", "口腔科", "皮肤科", "血常规", "尿常规", "尿微量蛋白（UMA)", "尿蛋白/尿肌酐比值", "大便常规", "肝功能", "空腹血糖", "糖化血红蛋白", "血脂", "肾功能", "心肌酶谱", "电解质", "甲状腺功能", "前列腺特异性抗原", "乙肝两对半", "同型半胱氨酸", "EB病毒抗体", "甲胎蛋白(AFP)定量", "癌胚抗原(CEA)定量", "CA-199", "CA724", "膀胱癌尿FISH测定", "CA125", "CA153", "鳞状细胞癌相关抗原 (SCC)", "神经元特异性烯醇化酶", "细胞角蛋白19片段", "胃部幽门螺杆菌检测", "妇检", "白带常规", "人乳头瘤病毒分型检测", "液基细胞学检查（女）", "心电图", "胸部X片", "胸部CT", "彩超(心脏)", "彩超(甲状腺)", "彩超(颈动脉)", "彩超(腹部+男泌尿系)", "彩超(子宫附件)", "彩超(乳腺)", "彩超(腹部+女泌尿系)", "彩色经颅多普勒", "骨密度检测", "无创动脉硬化检测", "胃肠镜", "免疫检测", "OCT", "肌电图", "冠脉CT", "心率变异检测", "心脏功能、心肌损伤检测", "血栓检测", "凝血功能检查", "肺功能检查", "血栓风险评估", "脑组织供氧评估", "头颅CT/核磁", "过敏原检测", "喉镜检查", "综述建议", "其他"]
```

- [ ] **Step 4: 创建抽取脚本 `scripts/gen_indicator_modules.py`**

```python
"""从 data/附件2-体检信息采集模板_v1.20_最新.xlsx row2 抽取模块序。

用法:
  python3 scripts/gen_indicator_modules.py            # stdout 打印 JSON
  python3 scripts/gen_indicator_modules.py --write backend/tests/fixtures/excel_row2_modules.json

注意:运行依赖 openpyxl(系统 python 已装),backend .venv 不装;产物为静态 JSON 提交入库。
"""
import argparse
import json

import openpyxl

XLSX = "data/附件2-体检信息采集模板_v1.20_最新.xlsx"
SHEET = "体检数据采集模板"


def extract_row2_modules(path: str) -> list:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET]
    cover = {}
    for mr in ws.merged_cells.ranges:
        if mr.min_row == 2:
            for c in range(mr.min_col, mr.max_col + 1):
                cover[c] = mr.min_col
    items = []
    for c in range(1, ws.max_column + 1):
        if cover.get(c, c) != c:
            continue
        v = ws.cell(row=2, column=c).value
        if v is None or str(v).strip() == "":
            continue
        items.append(str(v).strip())
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write")
    args = ap.parse_args()
    modules = extract_row2_modules(XLSX)
    print(json.dumps(modules, ensure_ascii=False, indent=1))
    if args.write:
        with open(args.write, "w", encoding="utf-8") as f:
            json.dump(modules, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("written:", args.write, "count:", len(modules))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 校验脚本产出与 fixture 一致**

Run: `python3 scripts/gen_indicator_modules.py > /tmp/row2_modules.json && python3 -c "import json;a=json.load(open('/tmp/row2_modules.json'));b=json.load(open('backend/tests/fixtures/excel_row2_modules.json'));print('MATCH', a==b, 'count', len(a))"`
Expected: `MATCH True count 70`

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_excel_module_order.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add scripts/gen_indicator_modules.py backend/tests/fixtures/excel_row2_modules.json backend/tests/core/test_excel_module_order.py
git commit -m "test: Excel row2 模块序快照防漂移"
```

---

### Task 3: `GET /reports/{id}` 注入 group + module_order

**Files:**
- Modify: `backend/app/modules/report/schemas.py:6-14,42-52`(ReportIndicatorSchema 加 `group`;ReportDetailResponse 加 `module_order`)
- Modify: `backend/app/modules/report/router.py:138-144`(indicators 列表加 group、顶层加 module_order)
- Test: `backend/tests/test_report_detail_grouping.py`

**Interfaces:**
- Consumes: `indicator_groups.group_indicators(rows: list[dict]) -> tuple[list[dict], list[str]]`(Task1)
- Produces: report detail 每条 indicator 带 `group`;顶层 `module_order`。

- [ ] **Step 1: 写失败测试 `backend/tests/test_report_detail_grouping.py`**(仿现有 `tests/test_report_parsed_name.py::_patch_detail_services` 模式)

```python
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser
from app.modules.report.router import router as report_router


def _client_with_indicators(indicator_names):
    app = FastAPI()
    app.include_router(report_router, prefix="/api/v1/reports")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="user", hospital_id="1", id_card_suffix="100001", name="测试1"
    )
    report = SimpleNamespace(
        id=1, task_id=1, name="测试1", parsed_name=None,
        gender=None, age=None, report_date=None, check_type=None,
        unit_name=None, created_at=datetime(2026, 1, 1),
    )
    inds = [SimpleNamespace(
        item_name=n, item_name_standard=None, item_code=None,
        result_value="1", unit=None, ref_range_low=None, ref_range_high=None,
        category=None,
    ) for n in indicator_names]
    patches = [
        patch("app.modules.report.router.service.get_report_detail", return_value=report),
        patch("app.modules.report.router.service.get_report_indicators", return_value=inds),
        patch("app.modules.report.router.service.get_task_status",
              return_value=SimpleNamespace(status="completed")),
    ]
    for p in patches:
        p.start()
    client = TestClient(app)
    try:
        r = client.get("/api/v1/reports/1")
    finally:
        for p in patches:
            p.stop()
    return r


def test_report_detail_indicators_carry_group():
    r = _client_with_indicators(["白细胞", "血红蛋白", "尿潜血", "肿瘤特异生长因子"])
    assert r.status_code == 200, r.text
    data = r.json()
    by_name = {i["item_name"]: i.get("group") for i in data["indicators"]}
    assert by_name["白细胞"] == "血常规"
    assert by_name["血红蛋白"] == "血常规"
    assert by_name["尿潜血"] == "尿常规"
    assert by_name["肿瘤特异生长因子"] is None
    assert data["module_order"] == ["血常规", "尿常规"]


def test_report_detail_module_order_excel_sequence():
    r = _client_with_indicators(["谷丙转氨酶", "血红蛋白", "舒张压"])
    assert r.status_code == 200, r.text
    # Excel 顺序:身高体重血压(9) < 血常规(103) < 肝功能(193)
    assert r.json()["module_order"] == ["身高体重血压", "血常规", "肝功能"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_detail_grouping.py -q`
Expected: FAIL(断言报错:`module_order` 不存在 / `group` 缺失)

- [ ] **Step 3: schema 加字段 `backend/app/modules/report/schemas.py`**

```python
class ReportIndicatorSchema(BaseModel):
    item_name: str
    item_name_standard: Optional[str] = None
    item_code: Optional[str] = None
    result_value: Optional[str] = None
    unit: Optional[str] = None
    ref_range_low: Optional[str] = None
    ref_range_high: Optional[str] = None
    category: Optional[str] = None
    group: Optional[str] = None
```

```python
class ReportDetailResponse(BaseModel):
    id: int
    task_id: Optional[int] = None
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    report_date: Optional[date] = None
    check_type: Optional[str] = None
    unit_name: Optional[str] = None
    indicators: List[ReportIndicatorSchema] = []
    module_order: List[str] = []
    created_at: datetime
```

- [ ] **Step 4: router 注入 `backend/app/modules/report/router.py`(替换 120-144 区域)**

```python
    indicators_rows = service.get_report_indicators(db, report_id)
    from app.core.indicator_groups import group_indicators
    indicator_dicts = [
        {"item_name": i.item_name, "item_name_standard": i.item_name_standard,
         "item_code": i.item_code, "result_value": i.result_value,
         "unit": i.unit, "ref_range_low": i.ref_range_low,
         "ref_range_high": i.ref_range_high, "category": i.category}
        for i in indicators_rows
    ]
    grouped, module_order = group_indicators(indicator_dicts)
    # 展示名:与列表一致——解析出真实姓名优先;解析中(未完成)不泄露账号锚定名;
    # 已完成但未抽出姓名→回退归属锚定名。
    task_status = None
    if report.task_id:
        task = service.get_task_status(db, report.task_id)
        task_status = task.status if task else None
    if report.parsed_name:
        display_name = report.parsed_name
    elif task_status in ("queued", "parsing"):
        display_name = None
    else:
        display_name = report.name
    return {
        "id": report.id, "task_id": report.task_id,
        "name": display_name, "gender": report.gender, "age": report.age,
        "report_date": report.report_date, "check_type": report.check_type,
        "unit_name": report.unit_name,
        "indicators": grouped,
        "module_order": module_order,
        "created_at": report.created_at,
    }
```

> 注意:被替换块开头的 `indicators = service.get_report_indicators(...)` 变量更名为 `indicators_rows`,避免与方法调用冲突;其余(任务状态、展示名逻辑)保持不变。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_detail_grouping.py -q`
Expected: PASS

- [ ] **Step 6: 回归相关既有测试**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_parsed_name.py tests/test_report_router_streamed.py tests/test_report_detail_grouping.py -q`
Expected: PASS(既有报告详情行为不变)

- [ ] **Step 7: 提交**

```bash
git add backend/app/modules/report/schemas.py backend/app/modules/report/router.py backend/tests/test_report_detail_grouping.py
git commit -m "feat: /reports/{id} 指标带 group 与 module_order"
```

---

### Task 4: `GET /interpretations/{id}` 注入 group + module_order

**Files:**
- Modify: `backend/app/modules/interpretation/schemas.py:7-15,33-46`(IndicatorJudgmentSchema 加 `group`;InterpretationResponse 加 `module_order`)
- Modify: `backend/app/modules/interpretation/router.py:71-86`(rows 注入 group 后构 schema,响应带 module_order)
- Test: `backend/tests/test_interp_detail_grouping.py`

**Interfaces:**
- Consumes: `indicator_groups.group_indicators`(Task1)
- Produces: interpretation detail 每条 indicator 带 `group`;顶层 `module_order`。

- [ ] **Step 1: 写失败测试 `backend/tests/test_interp_detail_grouping.py`**

```python
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser
from app.modules.interpretation.router import router as interp_router


def _client_with_judgments(item_names):
    app = FastAPI()
    app.include_router(interp_router, prefix="/api/v1/interpretations")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="user", hospital_id="1", id_card_suffix="100001", name="测试1"
    )
    interp = SimpleNamespace(
        id=1, report_id=1, overall_level="yellow",
        red_count=0, yellow_count=1, green_count=1, status="completed",
        summary_text="{}", summary_refs=[], quality_note=None,
        created_at=datetime(2026, 1, 1), completed_at=datetime(2026, 1, 1),
    )
    rows = [
        {"indicator_id": i + 1, "item_name": n, "result_value": "1",
         "deviation": "normal", "color_level": "green", "unit": None,
         "ref_range_low": None, "ref_range_high": None}
        for i, n in enumerate(item_names)
    ]
    patches = [
        patch("app.modules.interpretation.router.service.get_interpretation", return_value=interp),
        patch("app.modules.interpretation.router.service.get_judgments_with_indicator_detail",
              return_value=rows),
    ]
    for p in patches:
        p.start()
    client = TestClient(app)
    try:
        r = client.get("/api/v1/interpretations/1")
    finally:
        for p in patches:
            p.stop()
    return r


def test_interpretation_indicators_carry_group():
    r = _client_with_judgments(["白细胞", "血红蛋白", "游离甲状腺素(FT4)测定", "肿瘤特异生长因子"])
    assert r.status_code == 200, r.text
    data = r.json()
    by_name = {i["item_name"]: i.get("group") for i in data["indicators"]}
    assert by_name["白细胞"] == "血常规"
    assert by_name["血红蛋白"] == "血常规"
    assert by_name["游离甲状腺素(FT4)测定"] == "甲状腺功能"
    assert by_name["肿瘤特异生长因子"] is None
    assert data["module_order"] == ["血常规", "甲状腺功能"]


def test_interpretation_module_order_excel_sequence():
    r = _client_with_judgments(["甲胎蛋白(AFP)定量", "白细胞", "舒张压"])
    assert r.status_code == 200, r.text
    # Excel 顺序:身高体重血压(9) < 血常规(103) < 甲胎蛋白(AFP)定量(325)
    assert r.json()["module_order"] == ["身高体重血压", "血常规", "甲胎蛋白(AFP)定量"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_interp_detail_grouping.py -q`
Expected: FAIL(`module_order` / `group` 缺失)

- [ ] **Step 3: schema 加字段 `backend/app/modules/interpretation/schemas.py`**

```python
class IndicatorJudgmentSchema(BaseModel):
    indicator_id: int
    item_name: str
    result_value: Optional[str] = None
    unit: Optional[str] = None
    ref_range_low: Optional[str] = None
    ref_range_high: Optional[str] = None
    deviation: Optional[str] = None
    color_level: Optional[str] = None
    group: Optional[str] = None
```

```python
class InterpretationResponse(BaseModel):
    id: int
    report_id: int
    overall_level: Optional[str] = None
    red_count: int
    yellow_count: int
    green_count: int
    status: str
    summaries: InterpretationReportSchema = InterpretationReportSchema()
    references: List[CitationSchema] = []
    quality_note: Optional[str] = None
    indicators: List[IndicatorJudgmentSchema] = []
    module_order: List[str] = []
    created_at: datetime
    completed_at: Optional[datetime] = None
```

- [ ] **Step 4: router 注入 `backend/app/modules/interpretation/router.py`(替换 71-86 区域)**

```python
    rows = service.get_judgments_with_indicator_detail(db, interp.id)
    from app.core.indicator_groups import group_indicators
    grouped, module_order = group_indicators(rows)
    summaries = parse_summary_text(interp.summary_text)
    references = [CitationSchema(**r).model_dump() for r in (interp.summary_refs or [])]
    indicators = [IndicatorJudgmentSchema(**r) for r in grouped]
    return {
        "id": interp.id, "report_id": interp.report_id,
        "overall_level": interp.overall_level,
        "red_count": interp.red_count, "yellow_count": interp.yellow_count,
        "green_count": interp.green_count,
        "status": interp.status,
        "summaries": summaries,
        "references": references,
        "quality_note": interp.quality_note,
        "indicators": indicators,
        "module_order": module_order,
        "created_at": interp.created_at, "completed_at": interp.completed_at,
    }
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_interp_detail_grouping.py -q`
Expected: PASS

- [ ] **Step 6: 回归相关既有测试**

Run: `cd backend && .venv/bin/python -m pytest tests/ai/agents/test_interp_graph.py tests/test_interp_detail_grouping.py -q`
Expected: PASS(既有 interpretation schema 断言不破)

- [ ] **Step 7: 提交**

```bash
git add backend/app/modules/interpretation/schemas.py backend/app/modules/interpretation/router.py backend/tests/test_interp_detail_grouping.py
git commit -m "feat: /interpretations/{id} 指标带 group 与 module_order"
```

---

### Task 5: user-portal 报告详情页折叠分组 UI

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`
- 验证:tsc build(`cd frontend/packages/user-portal && npm run build`)

**Interfaces:**
- Consumes: 后端新增字段 `ind.group: string|null`、顶层 `module_order: string[]`(Task3/4);渲染沿用现有 `IndicatorRow`、`ColorBadge`。

- [ ] **Step 1: 顶部工具函数(加在 `ReportDetailPage.tsx` `COLOR_ORDER` 定义之后)**

```tsx
function sortByColor(items: any[]): any[] {
  return [...items].sort((a, b) =>
    (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));
}

function toGroups(indicators: any[], moduleOrder?: string[]) {
  const hasOrder = Array.isArray(moduleOrder) && moduleOrder.length > 0;
  if (!hasOrder) {
    // 旧后端无 module_order → 退化为今天的整体平铺(红黄绿优先)
    return { groups: [], flat: sortByColor(indicators) };
  }
  const groups = new Map<string, any[]>();
  const flat: any[] = [];
  for (const ind of indicators) {
    if (ind.group && moduleOrder.includes(ind.group)) {
      if (!groups.has(ind.group)) groups.set(ind.group, []);
      groups.get(ind.group)!.push(ind);
    } else {
      flat.push(ind);
    }
  }
  return {
    groups: moduleOrder.filter((g: string) => groups.has(g))
      .map((name) => ({ name, items: sortByColor(groups.get(name)!) })),
    flat: sortByColor(flat),
  };
}

function countLevels(items: any[]): { red: number; yellow: number; green: number } {
  const c = { red: 0, yellow: 0, green: 0 };
  for (const it of items) {
    const l = it.color_level;
    if (l === 'red' || l === 'yellow' || l === 'green') c[l] += 1;
  }
  return c;
}
```

- [ ] **Step 2: import Collapse + useState**(顶部 import 区,新增)

```tsx
import { useEffect, useState } from 'react';
import { Spin, Button, Popconfirm, message, Collapse } from 'antd';
```

- [ ] **Step 3: 组件内分组计算(替换现 `sortedIndicators` 三行 110-115)**

先把折叠开关 state 与其它 hooks 放一起(组件函数顶部现有 state 声明区,任何 `return` 之前):

```tsx
  const [openModules, setOpenModules] = useState<string[]>([]);
```

再替换原代码块:
```tsx
  const overallLevel = interpretation?.overall_level;
  // 优先用 interpretation.indicators(...)...
  const rawIndicators = interpretation?.indicators?.length ? interpretation.indicators : (report?.indicators || []);
  const sortedIndicators = [...rawIndicators].sort((a, b) =>
    (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));
```

替换为:
```tsx
  const overallLevel = interpretation?.overall_level;
  const rawIndicators = interpretation?.indicators?.length ? interpretation.indicators : (report?.indicators || []);
  const moduleOrder = interpretation?.module_order ?? report?.module_order;
  const { groups, flat } = toGroups(rawIndicators, moduleOrder);
  const totalCount = rawIndicators.length;
```

- [ ] **Step 4: 折叠渲染 JSX(替换 171-186 指标卡片块)**

原代码块(整段):
```tsx
      <div style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', padding: '0 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)' }}>
        {sortedIndicators.map((ind: any, idx: number) => (
          <IndicatorRow
            key={idx}
            item_name={ind.item_name}
            result_value={ind.result_value}
            unit={ind.unit}
            ref_range_low={ind.ref_range_low}
            ref_range_high={ind.ref_range_high}
            color_level={ind.color_level}
          />
        ))}
        {sortedIndicators.length === 0 && (
          <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-secondary)', fontSize: 13 }}>暂无指标数据</div>
        )}
      </div>
```

替换为:
```tsx
      <div style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', padding: '0 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)' }}>
        {groups.length > 0 && (
          <Collapse
            bordered={false}
            ghost
            expandIconPosition="end"
            activeKey={openModules}
            onChange={(keys) => setOpenModules((Array.isArray(keys) ? keys : [keys]) as string[])}
            items={groups.map(({ name, items }) => {
              const cnt = countLevels(items);
              return {
                key: name,
                label: (
                  <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', paddingRight: 8 }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{name}</span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span>{items.length}项</span>
                      {cnt.red > 0 && <span style={{ color: 'var(--color-red)', fontWeight: 600 }}>红区 {cnt.red}</span>}
                      {cnt.yellow > 0 && <span style={{ color: 'var(--color-yellow)', fontWeight: 600 }}>黄区 {cnt.yellow}</span>}
                      {cnt.green > 0 && <span style={{ color: 'var(--color-green)', fontWeight: 600 }}>绿区 {cnt.green}</span>}
                    </span>
                  </span>
                ),
                children: items.map((ind: any, idx: number) => (
                  <IndicatorRow
                    key={idx}
                    item_name={ind.item_name}
                    result_value={ind.result_value}
                    unit={ind.unit}
                    ref_range_low={ind.ref_range_low}
                    ref_range_high={ind.ref_range_high}
                    color_level={ind.color_level}
                  />
                )),
              };
            })}
          />
        )}

        {flat.length > 0 && (
          <div style={{ borderTop: groups.length > 0 ? '1px solid var(--color-border-light)' : 'none', marginTop: groups.length > 0 ? 8 : 0 }}>
            {flat.map((ind: any, idx: number) => (
              <IndicatorRow
                key={idx}
                item_name={ind.item_name}
                result_value={ind.result_value}
                unit={ind.unit}
                ref_range_low={ind.ref_range_low}
                ref_range_high={ind.ref_range_high}
                color_level={ind.color_level}
              />
            ))}
          </div>
        )}

        {totalCount === 0 && (
          <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-secondary)', fontSize: 13 }}>暂无指标数据</div>
        )}
      </div>
```

> 注意:`report?.module_order` 在旧 report 详情响应中不存在时为 `undefined`,`toGroups` 已按无 module_order 退化为整份平铺(保持现状)。若 `interpretation` 未生成时 UI 只显示 report 侧(无 module_order),行为与改动前一致。

- [ ] **Step 5: tsc build 验证**

Run: `cd frontend/packages/user-portal && npm run build`
Expected: 构建成功,无 TS 报错(若有 `activeKey` 类型告警,`onChange`/`activeKey` 按 antd v5 `CollapseProps` 类型兼容写法处理)

- [ ] **Step 6: 手工验收清单(需运行环境,后端重启后真实验证)**

- [ ] 打开一份已解读真实报告:指标明细区默认只显示若干折叠模块标题行,总行数远小于指标总数
- [ ] 标题行格式:`模块名  N项`(含红/黄/绿时右侧出现对应色计数),模块顺序与 Excel row2 一致
- [ ] 点开「血常规」:看到该模块全部指标(数值/单位/参考区间/色标),组内红黄绿优先排序
- [ ] 无法归类的指标(如 `肿瘤特异生长因子`)显示在所有折叠模块之下,无折叠箭头、直接平铺且带色标
- [ ] 顶部红/黄/绿总计数条、AI 解读卡、与历史报告对比卡与改动前一致
- [ ] 空指标报告仍显示「暂无指标数据」

- [ ] **Step 7: 提交**

```bash
git add frontend/packages/user-portal/src/pages/ReportDetailPage.tsx
git commit -m "feat: 用户端报告详情指标按 Excel 模块折叠展示"
```
