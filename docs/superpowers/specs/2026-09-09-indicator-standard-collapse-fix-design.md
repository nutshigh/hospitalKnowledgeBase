# 指标标准名子串吞噬修复:归一化打标 + 走势主项过滤 + 存量回填

日期:2026-09-09

## 背景与问题

患者端「我的健康档案」`/profile/overview` 的「指标走势」里,u_zhangsan(`011234·张三`,库 `hospital_1`)的「血小板计数（PLT）」系列出现了 **11 个点**,而不是按「只取最近 3 份报告」应有的 ≤3 点。

复现(hospital_1,窗口报告 rid 23 / 27 / 1):

```
血小板计数（PLT）: 11 点 = rid23 5点(0.29/319/9.1/17.2/9.2) + rid27 5点 + rid1 1点(210)
```

**根因(两层)**:

1. **数据层**:`app/core/term_normalizer.py` 的 `_STANDARD_MAP` 用 `if alias in cleaned` 子串命中。`"血小板" → 血小板计数（PLT）` 会吞掉名字含「血小板」的所有指标;`"红细胞"` 吞红细胞压积/平均体积/MCH/MCHC/RDW;`"血红蛋白"` 吞平均血红蛋白含量/浓度;`"低密度脂蛋白"` 吞小而密低密度脂蛋白;`"尿酸"` 吞尿酸碱度等。同一父标准名下挂了多个**真实不同**的指标(单位/量纲不同:0.29 `%` 与 319 `10^9/L`)。
2. **消费层**:`user_profile/service.py` 的 `get_overview`(line 87)与 `_series`(line 276)以 `item_name_standard or item_name` 为系列 key,不校验「同一报告内同 key 是否为同一真实指标」,于是把多行不同指标并成一条线。

## 决策(用户确认)

- 修复范围 = **源头(term_normalizer)+ 防线(聚合守卫)+ 存量回填**。
- 指标走势 **只展示主项指标**(primary);血常规等面板的子项指标(血小板比积/平均体积/分布宽度/大血小板比率、红细胞压积/MCV/MCH/MCHC/RDW、小而密 LDL 等)**不进走势**,报告详情页不受影响。
- 「近期健康变化总览」(`/profile/change-overview` 的 `key_indicators` + AI 总结)**保留全量**(含子项),不回退 —— 子项异常对健康总结仍有意义;清洗后子项各有独立标准名,不再混入父项。
- 「主项/子项」由 **term_normalizer 词表打标** 定义(单一事实来源),不另立聚合层清单。
- 存量回填只洗**当前解析管线**产出的库(`hospital_1` / `hospital_H001` / `hospital_H002`);旧命名风格库 `hospital_H003` / `hospital_H004` 不动。

## 改动设计

### 1. `app/core/term_normalizer.py` 改造:全名匹配 + 词表打标

**匹配语义**:
- 别名改为**整名精确匹配**,不再 `alias in cleaned` 子串包含 → 结构性杜绝「短别名吞长词」这一类问题(血小板比积、尿酸碱度、尿白细胞酯酶等不再被吞)。
- raw 名先清洗(去空格/全角空格),再剥**尾缀英文码括号**(如 `血小板分布宽度（CV）`、`血红蛋白(HGB)`)得到 base;**中文限定语括号不剥**(如 `（镜检）`、`（尿）`),避免「蛋白质（尿）」误落到血清总蛋白。
- `base` 命中词表 alias → 返回 canonical;未命中 → `standard = raw` 原名透传(不吞不并)。

**词表结构**:

```python
@dataclass(frozen=True)
class CanonTerm:
    standard: str
    primary: bool = True   # False = 子项(derivative),走势隐藏
```

alias → CanonTerm。保留现 `_STANDARD_MAP` 里 17 个主项 canonical 字符串不变(避免无谓 churn),其余为主项未命中透传。新增子项 canonical(全宽括号、与现管线风格一致):

| canonical | 覆盖 alias |
|---|---|
| `血小板比积（PCT）` | 血小板比积 / 血小板比容 / 血小板压积 |
| `血小板平均体积（MPV）` | 血小板平均体积 / 平均血小板体积 / 血小板平均容积 / 平均血小板容积 |
| `血小板分布宽度（PDW）` | 血小板分布宽度 / 血小板体积分布宽度 |
| `大血小板比率（P-LCR）` | 大血小板比率 / 大血小板数 / 大型血小板比率 |
| `红细胞压积（HCT）` | 红细胞压积 / 红细胞比容 / 红细胞比积 |
| `平均红细胞体积（MCV）` | 平均红细胞体积 / 红细胞平均体积 / 平均红细胞容积 |
| `平均红细胞血红蛋白量（MCH）` | 平均红细胞血红蛋白量 / 平均红细胞血红蛋白含量 / 平均血红蛋白含量 / 平均血红蛋白量 |
| `平均红细胞血红蛋白浓度（MCHC）` | 平均红细胞血红蛋白浓度 / 平均血红蛋白浓度 |
| `红细胞分布宽度（RDW-CV）` | 红细胞分布宽度（CV）相关变体(分布宽度CV/变异系数/体积分布宽度CV) |
| `红细胞分布宽度（RDW-SD）` | 红细胞分布宽度（SD）相关变体(分布宽度SD/标准差/体积分布宽度SD) |
| `有核红细胞计数（NRBC）` | 有核红细胞计数 / 有核红细胞数 / 有核红细胞百分比 |
| `小而密低密度脂蛋白胆固醇（sdLDL）` | 小而密低密度脂蛋白胆固醇 |

主项 PLT/RBC/Hb/WBC 等 canonical 词条补充全名别名(如 `血小板` / `血小板计数` → 血小板计数（PLT）;`血红蛋白(HGB)` base=血红蛋白 → 血红蛋白（Hb）),保证现有主项仍被命中。

**公开 API**:
- `normalize_item_name(raw) -> (standard, code)` 保留索引 0 语义(standard=canonical 或原名),`normalize_indicators` 去重逻辑不变。
- 新增 `resolve_canonical(raw) -> CanonTerm | None`(供回填脚本)。
- 新增 `is_child_item(item_name) -> bool`(走势过滤;基于 raw 全名解析,不依赖已存库的标准名)。

### 2. 走势只留主项 + 聚合防线(`app/modules/user_profile/service.py`)

**主项过滤(仅 `get_overview` 的指标走势)**:
- 指标循环中,`is_child_item(ind.item_name)` 为 True 的行**不进 `by_key`**(不生成系列)。
- 效果:PLT 系列只剩真「血小板计数」行,窗口 N 份报告 ⇒ ≤N 点。子项在报告详情仍可见(不动报告模块)。
- `is_child_item` 基于 raw `item_name` 判定,故**未回填前**(DB 里子项仍挂在父标准名下)走势也已正确剔除子项 —— 主项过滤不依赖存量清洗。
- `abnormal_distribution`(后端字段保留,前端已不消费)、`user_summary` 语义不变。
- `_series`(change-overview)与 `_rank_key_indicators` **不加**主项过滤(全量保留,用户决策)。

**聚合防线(守卫,给 `get_overview` 与 `_series` 共用)**:
- 守卫规则:同一系列内**同一 report_id 不允许出现 >1 个点**。若出现 → 说明仍有身份未分干净的脏行(如未回填库或未来 LLM 新造名),将该报告从该系列按 `item_name` 拆成独立系列(即不合并不同真实指标),并 `logger.warning(...)` 记录。
- 防线作为对源头修复的兜底;干净数据下守卫不触发、行为不变。

### 3. 存量回填脚本(只洗 `hospital_1` / `hospital_H001` / `hospital_H002`)

新增 `backend/scripts/manual_migrations/007_fix_indicator_standard.py`(Python + SQLAlchemy,复用 app.config 连接):

- 对每个目标库:`SELECT DISTINCT item_name FROM report_indicator WHERE item_name IS NOT NULL AND item_name <> ''`;
- 用新词表 `resolve_canonical(item_name)` 算目标 `item_name_standard`(未命中 → 原名透传,即与 `normalize_item_name` 同语义);
- `UPDATE report_indicator SET item_name_standard=:new WHERE item_name=:name AND item_name_standard IS DISTINCT FROM :new`,逐 item_name 汇总改动行数并打印;
- 默认 `--dry-run` 只打印不落库;`--apply` 才执行。不碰 item_name / item_code / 数值 / 判定表;不动 H003 / H004。
- 修后 u_zhangsan 走势:子项行被主项过滤剔除,PLT 系列 = 3 点(319/319/210)。

### 4. 测试

- `tests/core/test_term_normalizer.py`:
  - 全名匹配不再吞子项(`血小板比积` ≠ PLT;`尿酸碱度` ≠ 尿酸);
  - 尾缀英文码剥离(`血红蛋白(HGB)` → 血红蛋白（Hb）),中文括号不剥;
  - 子项 canonical + primary=False;主项 alias 命中;未命中透传原名。
- `tests/user_profile/test_service.py`:同份报告含父+子指标时 `indicator_trends` 只含主项、父系列每报告 ≤1 点(用 u_zhangsan 同构数据复现 11 点场景 → 断言 ≤3)。
- `tests/user_profile/test_change_overview.py`:变化总览 `key_indicators` 仍含子项(全量不回退)。
- 防线守卫单测:同 key 同报告多行 → 拆系列 + warning。

## 不做 / 保留

- H003 / H004 旧命名风格库**不回填、不修**(其子项合并现象仍在),如日后需要单独立项(结构与现管线命名风格不同)。
- `interpretation` / `rules_engine` 逻辑不改;但新解析数据的标准名修正后,`_match_key_indicator`(rules_engine.py:67)不会再拿 PLT 规则误匹配血小板子项 —— 属期望的解读输入语义修正(只影响新 parse 与已回填库的后续读取)。
- 不改表结构 / DDL / 前端 / config。
- AGENTS.md 是否需要补充「子项不进入指标走势」口径,在实现阶段视改完后实际语义再定(不影响本设计落点)。

## 验证

1. `cd backend && .venv/bin/python -m pytest tests/user_profile tests/core/test_term_normalizer.py -q`。
2. `backend/scripts/manual_migrations/007_fix_indicator_standard.py --dry-run`(对 hospital_1)预演,u_zhangsan 相关 item_name 改动行数与预期一致后再 `--apply`。
3. 手工复现:对 hospital_1 跑 `/profile/overview`(u_zhangsan),`indicator_trends` 中 PLT 系列点数为 3、无子项系列;`/profile/change-overview` key_indicators 仍按需包含子项。
4. 回归:全量 `tests/user_profile` + `tests/core`。
