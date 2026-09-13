# 走势/总览展示窗口内全部红黄指标(含子项)

日期:2026-09-10

## 背景

前置两次改动叠加后,患者端「我的」页血小板相关信息整块消失:

- 2026-09-09「指标走势只留主项」:子项(血小板比积/平均体积/分布宽度/大血小板比率、HCT/MCV/MCH/MCHC/RDW、小而密 LDL 等)被 `is_child_item` 从走势剔除;
- 2026-09-10「总览对齐走势」:key_indicators 也改为仅主项 + 仅红/黄。

u_zhangsan 实测:血小板计数窗口内 3 点(319/319/210)全绿 → 按「只显示红/黄」不出现;而唯一的血小板异常(血小板比积 0.29 黄、血小板平均体积 9.1 黄)来自子项 → 被隐藏。结果走势与变化总览都不再有任何血小板内容。

## 决策(用户确认)

- **走势/变化总览都展示「窗口内出现过红/黄的每一项指标」,子项不再被剔除**;每个指标(含子项)以自身规范名独立成系列,每报告 ≤1 点。
- 正常(绿色)指标仍不展示 —— 包括本身正常的`血小板计数`(全绿则不出现)。
- 其余已对齐口径保持不变:共享入选(`_has_abnormal`)/排序(`_trend_sort_key`)/上限(`PROFILE_TREND_MAX_ITEMS` 默认 10);取窗差异(走势=最近 N 份报告,总览=最近 N 份已完成解读)保留。
- 上限保持 10;前端不改(走势 slice 10、总览 5 条折叠+展开)。

## 设计

仅改 `backend/app/modules/user_profile/service.py`:

1. `get_overview` 指标循环:删除 `if is_child_item(ind.item_name or ""): continue`。
2. `_rank_key_indicators`:删除 `if is_child_item(standard): continue`(及其 `standard` 变量若不再使用)。
3. `service.py` 不再引用 `is_child_item` → 从 `from app.core.term_normalizer import ...` 移除该导入(保留 `normalize_item_name`,供 `_split_item_name_collisions` 使用)。
4. `term_normalizer.is_child_item` 函数**保留**、单测保留(未来仍可用),本次只是不再被 profile 消费。

不改:共享工具 `_has_abnormal` / `_points_range` / `_trend_sort_key`、`_severity`、上限截断、`_split_item_name_collisions` 防线、缓存校验(signature + fingerprint)、数据表/DDL。

数据侧无需迁移:

- 已回填库(hospital_1/H001/H002):子项已有独立标准名(`血小板比积（PCT）` 等),直接各成系列;
- 未回填库(H003/H004):`_split_item_name_collisions` 对「同报告同 key 多 item_name」按 `(item_name, unit)` 拆分,并把 `item_name_standard` 规范化为子项 canonical → 同样各成系列、不并线。

## 测试

- 改写 `tests/user_profile/test_service.py::test_get_overview_trends_only_primary_items`:反转语义 —— 当子项在窗口内有红/黄判定时,`indicator_trends` **必须**出现该子项独立系列(规范名),且与主项系列并列;子项无判定时仍不出现。
- 改写 `tests/user_profile/test_change_overview.py::test_key_indicators_exclude_child_items` → `..._include_abnormal_child_items`:子项有红/黄 → 进 key_indicators(规范名);无判定 → 不进。
- 新增:同窗口下 `get_overview` 与 `get_change_overview` 的指标名列表一致,且包含异常子项(如 `血小板比积（PCT）`),每系列每报告 ≤1 点。
- 既有「上限 10」「排序/子项数据拆分」「缓存 fingerprint」用例保持通过。
- `tests/core/test_term_normalizer.py::test_is_child_item_flags` 保持(函数未删)。

## 文档

AGENTS.md 2026-09-10 两条口径改写为:

- 「走势/总览展示窗口内红黄指标(含子项),各以规范名独立成系列,上限 10;正常指标(全绿,如血小板计数)不出现」;
- 删除「走势只留主项 / 子项被 `is_child_item` 从走势剔除」的表述;`is_child_item` 保留但不再被 profile 使用。

## 上线

- 规则口径再次变化,旧 `comparison_summary` 缓存口径过时 → 对全部 tenant 库**再跑一次** `backend/scripts/manual_migrations/008_clear_change_overview_cache.sql`;
- 重启 `:8000` backend(否则旧进程继续按旧口径写缓存)。

## 预期效果(u_zhangsan)

- 走势与变化总览一致,新增子项线:`血小板比积（PCT）`(0.29 黄)、`血小板平均体积（MPV）`(9.1 黄)等;
- `血小板计数`(全绿)仍不出现。
