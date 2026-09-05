# 报告解读后指标折叠展示(按体检采集模板模块)设计

**日期**:2026-09-05
**状态**:Draft(已与用户对齐 §0/§1/§2,待 review)
**前置**:
- 分类规则模板:`data/附件2-体检信息采集模板_v1.20_最新.xlsx`(row2 = 模块/检查大类,row3 = 模块内指标)
- 工程约束:`AGENTS.md`

---

## 0. 目标与边界

### 目标
用户门户「报告详情页」在解读完成后展示指标明细时,不再把上百条指标一行行平铺,而是**按体检采集模板的模块折叠展示**:默认全部折叠成"模块标题行",用户点开某个模块才能看到该模块包含的指标及其结果;无法归入任何模块的指标**直接平铺**展示。

### 产品规则(已与用户对齐)
1. **默认全部折叠**,只显示各模块标题行。
2. **标题行内容**:模块名 + 项数;模块内有红/黄异常项时叠加彩色计数徽标(如 `黄区2`)。
3. **可折叠对象**:指标能按规则归入 Excel row2 某个模块 → 折叠进该模块;归不了模块的 → 平铺区直接展示,不再套一层"其他"折叠。
4. **归类接受同义词/近义扩充**:报告实际指标名与 Excel 表头不完全一致(Excel"白细胞数(WBC)" vs 报告"白细胞";Excel 血脂模块未列"小而密低密度脂蛋白" 等),允许别名关键字规则把近义指标归入对应模块。
5. **排序**:模块之间按 Excel row2 从上到下顺序;模块内指标沿用现有红黄绿(red→yellow→green)优先排序;平铺区保持红黄绿排序放在所有折叠模块**之后**。

### 范围内
- 后端:新增指标→模块分类器,报告详情/解读详情两个读接口给每条指标带 `group`,解读详情再带顶层 `module_order`
- 前端:仅 `user-portal` 报告详情页的指标明细区改为折叠分组渲染
- 后端 pytest 单测(分类器 + 接口字段契约)

### 范围外(YAGNI)
- 医生门户/管理门户报告详情(其"指标明细"Table 保持现状;分类字段已在响应中,日后可零后端改动接入)
- "与历史报告对比"(ComparisonCard)指标差异列表
- 折叠状态持久化(每次进入页面默认全折叠)
- 解析阶段写库 `report_indicator.category`(现列未用,保持不动,不回流 DB)
- 极端歧义项的语义消歧(如下午尿沉渣与血常规同名"红细胞",解析阶段已丢失章节上下文,靠名称无法可靠区分,按默认模块归类即可)

---

## 1. 后端:指标→模块分类器

### 1.1 新增 `backend/app/core/indicator_groups.py`
纯函数、无 IO、无三方依赖,内含:

- **`MODULE_ORDER: list[str]`**:有序模块名,**顺序 = Excel row2 从上到下的模块顺序**(基本信息→身高体重血压→内科→外科→眼科→…→血常规→尿常规→…→综述建议→其他)。
  - 初始内容由脚本 `scripts/gen_indicator_modules.py` 从 xlsx 抽取 row2 合并表头自动生成并写入 `backend/app/core/indicator_groups.py`(实现时以 diff 核对),避免手抄 60+ 模块名出错;Excel 升版重跑脚本核对 diff。
- **规则表**(每条):`{"module": <MODULE_ORDER 内名称>, "include": [...], "exclude": [...]}`:
  - `include`:命中即倾向归入该模块的关键字/别名(从真实库 distinct `item_name` 提炼,含 Excel row3 指标名的可辨识片段 + 报告侧同义词)
  - `exclude`:命中则不归入(解决歧义,如 CBC `白细胞` 排除 `尿白细胞`/`镜检`/`白带`)
- **`classify(item_name: str) -> Optional[str]`**:
  1. 归一化:去首尾/内部空白;
  2. 依 `MODULE_ORDER` 顺序遍历规则;某规则 `include` 中任一项为 item_name 子串 且 所有 `exclude` 均不命中 → 返回该模块名;
  3. 全不命中 → `None`(前端平铺)。
- **`group_indicators(items: list[dict]) -> tuple[dict, list[str]]`**:对 `[{item_name,...}]` 批量 classify,返回 `(items(每条注入 group), module_order(按 MODULE_ORDER 序去重、只含实际出现的模块))`。

> 规则顺序即优先级,靠前模块先匹配;歧义项以规则作者人工裁决为准,注释写明原因。

### 1.2 Excel 抽取脚本 `scripts/gen_indicator_modules.py`
- `openpyxl` 读 `data/附件2-体检信息采集模板_v1.20_最新.xlsx`「体检数据采集模板」sheet row2,按合并表头(含 A1:B1 之后的 row2 连续组)还原各模块及其横向列区间 → 输出有序模块名列表(供 §1.1 落地)。
- 幂等/只读,不写库;实现时以 diff 形式人工核对。

### 1.3 接口接线
**两个读接口都加 `group`**(UI 取 `interpretation.indicators`,退化时取 `report.indicators`,两者都须带):

| 接口 | 现状返回点 | 变更 |
|------|-----------|------|
| `GET /reports/{id}` | `backend/app/modules/report/router.py` get_report_detail 的 indicators 列表推导 | 每条 dict 加 `group`;顶层加 `module_order: list[str]` |
| `GET /interpretations/{id}` | `backend/app/modules/interpretation/router.py` get_interpretation | 每条加 `group`;顶层加 `module_order: list[str]` |

schema 变更(字段均可选,向后兼容,`group: Optional[str] = None`;`module_order: list[str] = []`):
- `backend/app/modules/report/schemas.py`:`ReportIndicatorSchema` 加 `group`;`ReportDetailResponse` 加 `module_order`
- `backend/app/modules/interpretation/schemas.py`:`IndicatorJudgmentSchema` 加 `group`;`InterpretationResponse` 加 `module_order`

实现:router 拿到的指标 dict 列表丢给 `classify`/`group_indicators` 统一注入(分类只依赖 `item_name`,report 侧用 `i.item_name`,interp 侧用 `j.item_name`,两处各自已有,无需再查表)。

### 1.4 后端测试(`backend/tests/core/test_indicator_groups.py` 及接口契约)
- `classify` 单测:别名命中(如 `游离甲状腺素(FT4)测定`→甲状腺功能、`小而密低密度脂蛋白`→血脂、`平均红细胞体积`→血常规)、排除规则(如含 `尿白细胞` 不进血常规)、归一化(全角/半角空格、首尾空白)、英文缩写保留、未命中→`None`
- `MODULE_ORDER` 与 Excel row2 顺序一致(脚本产出值等于已落地常量,防漂移)
- 接口契约(仿 `backend/tests/test_report_parsed_name.py` 模式:sqlite in-memory + FastAPI TestClient + 依赖 override / patch router 依赖 service):stub `get_interpretation`/`get_judgments_with_indicator_detail`(或 report 侧 `get_report_indicators`/`get_task_status`)返回含已知 `item_name` 的行,断言 `/interpretations/{id}` 与 `/reports/{id}` 返回的每条 indicator 带 `group`、顶层 `module_order` 为按 Excel 序出现的模块名、无法归类的项 `group is None`

---

## 2. 前端:user-portal 报告详情页折叠 UI

**改动文件**:`frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`(仅指标明细区块;`IndicatorRow` 组件不动)

1. **取数**:沿用 `interpretation?.indicators?.length ? interpretation.indicators : (report?.indicators || [])`;每条已含 `group`。
2. **分组与排序**:
   - 模块桶顺序 = 响应顶层 `module_order`;桶内指标按现有 `COLOR_ORDER`(red/yellow/green)稳定排序;
   - `group` 为 `null`(或不在 `module_order` 内)的指标进**平铺区**。
3. **折叠渲染**(antd `Collapse`,`defaultActiveKey=[]` 默认全折叠,`bordered=false` 贴合现有卡片风格):
   - 每模块一个 Panel;标题行 = `模块名` + 右侧 `N项` + (红/黄>0 时)彩色计数徽标,如 `黄区2`、`红区1`;
   - Panel 内容 = 桶内指标逐行渲染现有 `IndicatorRow`(带单位/参考区间/色标,原样)。
4. **平铺区**:折叠模块区下方,不套折叠标题,直接渲染普通行(保持红黄绿排序),与折叠块间加分隔线。
5. **兼容回退**:任一响应缺 `group`/`module_order`(旧缓存/异常)时,整区退化为今天的行为——全部平铺,不报错。
6. 顶部红/黄/绿总计数条、`InterpretationReportCard`、`ComparisonCard`、处理中态逻辑均**不动**。

前端验证(仓库无前端单测基建):`npm run build`(tsc)通过 + 手工对照真实报告验收(见 §3)。

---

## 3. 验收清单

1. 一份已解读真实报告:指标明细区折叠成若干模块标题行,数量远小于指标总数;默认全部收起。
2. 模块顺序与 Excel row2 一致;点开任一模块能看到该模块的指标与结果/单位/参考区间,顺序红黄绿优先。
3. 含红/黄项的模块标题行出现对应计数徽标。
4. 报告独有、无法归类的指标(如 `肿瘤特异生长因子`)在折叠区之下平铺展示,且仍带色标。
5. 回归:处理中/失败态、红黄绿总计数条、AI 解读、与历史报告对比均与改动前一致。
6. `backend` pytest 全绿;前端 tsc build 通过。

---

## 附:A 模块归类规则初稿(以实际 distinct 指标名为准在实现期精修)

> 规则表以"模板块名 include/exclude"表达,实际条目以实现期用多 tenant DB distinct `item_name` 扫描校准、保证 无冲突 + 低未归类率。以下为代表性示例。

| 模块(Excel row2) | include 示例 | exclude 示例 |
|------|------|------|
| 身高体重血压 | 身高、体重指数、收缩压、舒张压、体重 | — |
| 内科 | 肺部、腹部、心率、心律、呼吸 | — |
| 外科 | 淋巴结、甲状腺结节、脊柱、四肢、关节 | 彩超、超声 |
| 眼科 | 视力、眼压、眼睑、结膜、角膜、瞳孔、眼底、晶状体、辨色 | — |
| 耳鼻喉科 | 耳、鼻、咽、扁桃体、喉、鼓膜、听力 | 尿、粪 |
| 口腔科 | 牙、口腔、牙龈、颞颌、涎腺 | — |
| 皮肤科 | 皮肤、皮疹、色素 | — |
| 血常规 | 白细胞、红细胞、血红蛋白、血小板、血细胞比容、中性粒、淋巴、单核、嗜酸、嗜碱、平均红细胞、平均血小板、红细胞体积分布、血小板压积 | 尿、镜检、白带、脑脊液 |
| 尿常规 | 尿胆原、尿蛋白、尿糖、尿酮、尿潜血、尿白细胞、尿红细胞、尿比重、尿液、酸碱度、结晶、管型、亚硝酸盐、维生素C、尿微量白蛋白 | 血 |
| 大便常规 | 粪、便潜血、粪便 | — |
| 肝功能 | 总胆红素、直接胆红素、间接胆红素、转氨酶、谷草、谷丙、γ-谷氨酰、总蛋白、白蛋白、球蛋白、碱性磷酸酶、总胆汁酸、胆碱酯酶、前白蛋白 | — |
| 血糖 | 空腹血糖、葡萄糖、胰岛素 | 尿 |
| 糖化血红蛋白 | 糖化血红蛋白 | — |
| 血脂 | 胆固醇、甘油三酯、高密度脂蛋白、低密度脂蛋白、载脂蛋白、小而密 | — |
| 肾功能 | 尿素、肌酐、尿酸、胱抑素C、肾小球滤过率 | — |
| 心肌酶谱 | 肌酸激酶、乳酸脱氢酶、羟丁酸、肌钙蛋白、CK-MB、心肌 | — |
| 电解质 | 钾、钠、氯、钙、镁、磷、二氧化碳 | 尿、尿液 |
| 甲状腺功能 | 三碘甲状腺原氨酸、甲状腺素、T3、T4、促甲状腺、TSH、抗过氧化物酶 | 彩超、超声 |
| 肿瘤标志物 | 甲胎蛋白、癌胚抗原、CA125、CA153、CA199、CA724、鳞状细胞、神经特异性烯醇化酶、细胞角蛋白、肿瘤 | — |
| 前列腺特异抗原 | 前列腺特异 | — |
| 幽门螺杆菌 | 幽门螺杆菌 | — |
| 免疫检测 | 免疫球蛋白、补体、类风湿因子、C反应蛋白、血沉、抗核抗体、抗链球菌 | 尿 |

> 注:row2 中"基本信息/综述建议/其他"及体格/影像检查类(彩超/CT/心电图等,多数以"检查所见/结论"文本形态出现,不在 numeric 指标内)是否在分类表设规则,取决于真实数据;未命中者自然落入平铺区,不强制覆盖。
