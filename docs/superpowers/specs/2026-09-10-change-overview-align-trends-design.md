# 「近期健康变化」与「指标走势」入选规则/条数对齐

日期:2026-09-10

## 背景

用户端「我的」页同一屏有两块指标列表,内容不一致:

- **指标走势**(`/profile/overview` → `get_overview.indicator_trends`):最近 N 份报告、**仅主项**(血常规子项被 `is_child_item` 过滤)、窗口内任一点红/黄即入选,排序=最近异常红>黄,同级按极差降序;后端返全量,前端 `slice(0,10)`。
- **近期健康变化**(`/profile/change-overview` → `_rank_key_indicators`):最近 N 份**已完成解读**报告、保留全量(含子项)、要求出现于 ≥2 份报告且(红/黄或 |delta_pct|≥5),排序=最近异常红>黄,同级按 |delta_pct| 降序;后端 `key_indicators[:5]`。

以 u_zhangsan 为例:走势显示 7 条(收缩压/舒张压/TC/LDL-C/TG/体质指数/肌酸激酶),总览只显示 5 条(TG/LDL/TC/舒张压/收缩压),体质指数/肌酸激酶因「5 条上限 + delta=0 排后」被截掉;子项(MPV/PCT)在总览候选池里但同样在 5 条外。

## 决策(用户确认)

1. **入选规则完全照搬走势**:仅主项(子项隐藏)+ 窗口内任一点红/黄即入选;去掉「≥2 份报告」与「|delta_pct|≥5」两个额外门;排序改为与走势同款 `(最近异常红>黄, -极差, item_name)`。
2. **条数上限对齐 = 10**(走势前端展示上限),新增配置项;两处后端都按它截断。
3. **前端展示保留现状**:变化总览仍默认 5 条 + 「展开全部」;走势仍 `slice(0,10)`。
4. **取窗不动**(只对齐规则+上限):走势=最近 N 份报告(不限解读状态);总览=最近 N 份已完成解读报告。此为已知残留差异。
5. 规则语义变更必须让旧 `comparison_summary` 缓存失效 —— **不引入缓存版本 key**,用一次性清理清空存量缓存(缓存可再生,无数据损失);后续标准名变更仍由既有 `fingerprint` 自动失效。

## 设计

### 1. 共享入选/排序小工具(`backend/app/modules/user_profile/service.py`)

新增/复用模块级函数,作为唯一事实来源:

```python
def _has_abnormal(points: list[dict]) -> bool:
    """窗口内任一点红/黄。"""
    return any(p.get("color") in ("red", "yellow") for p in points)


def _severity(points: list[dict]) -> int:
    """最近一次红/黄(红=0,黄=1),否则 2。(已有函数,复用)"""


def _points_range(points: list[dict]) -> float:
    """数值极差 max-min;经 _try_float 兼容 get_overview(float) 与 _series(str);
    无有效数值返回 0.0。"""


def _trend_sort_key(item: dict):
    """统一排序键:最近异常红>黄,同级按极差降序,再按指标名。"""
    pts = item["points"]
    return (_severity(pts), -_points_range(pts), item.get("item_name") or "")
```

- `get_overview`:删除内嵌 `_abnormal_sev` / `_range` / `_SEV`;过滤改用 `_has_abnormal`;排序改用 `_trend_sort_key`(**补 item_name 兜底**,与走势完全一致);返回前 `[:settings.PROFILE_TREND_MAX_ITEMS]`。
- `_rank_key_indicators`:遍历 `_series(db, window)` 时
  1. `is_child_item(item["item_name_standard"] or item["item_name"] or "")` 为真 → 跳过子项;
  2. 入选条件改为 `_has_abnormal(points)`;
  3. 排序改用 `_trend_sort_key`;
  4. 保留返回字段 `item_name / unit / latest_value / latest_color / direction / delta_pct / points`(单点系列 `delta_pct=None`、`direction=None`)。
- `_endpoint_pct` / `trend_direction` 保留(仅作字段填充,不再决定入选/排序)。

### 2. 条数上限配置

- `backend/app/config.py` 新增:

```python
# User Profile:指标走势 / 变化总览关键指标最多展示条数
PROFILE_TREND_MAX_ITEMS: int = Field(default=10, ge=1, description="指标走势/变化总览关键指标最多展示条数")
```

- `backend/.env.example` 增注释 `# PROFILE_TREND_MAX_ITEMS=10`。
- `get_overview` 返回 `indicator_trends[:settings.PROFILE_TREND_MAX_ITEMS]`。
- `get_change_overview` payload `key_indicators[:settings.PROFILE_TREND_MAX_ITEMS]`(原 `[:5]`)。
- `build_change_prompt` 内部 `key_indicators[:8]` 保持不变(LLM 上下文上限,不属于展示条数)。

### 3. 前端(不改代码)

- `ChangeOverviewCard.tsx`:后端现在最多返回 10 条,`key_indicators.length > 5` 时「展开全部」生效(此前后端 5 条永不触发展开)。
- `ProfilePage.tsx`:`filtered.slice(0, 10)` 不变。

### 4. 存量缓存一次性清理(不加版本 key)

规则口径变更未改动 `report_id/interp_id/item_name_standard`,旧缓存会被原样命中 → 上线时**一次性清空**存量缓存即可(缓存可再生,无数据损失);保留既有 `signature` + `fingerprint` 校验应对未来的标准名变更,不再新增版本字段。

- 新增 `backend/scripts/manual_migrations/008_clear_change_overview_cache.sql`:

```sql
-- 清空「近期健康变化」缓存(JSON)与退役的旧双报告对比纯文本;
-- 属可再生缓存,下次访问 /profile/change-overview 会自动重算写回,无数据损失。
UPDATE report_interpretation SET comparison_summary = NULL WHERE comparison_summary IS NOT NULL;
```

- 上线步骤:对全部 tenant 库执行一次(hospital_1 / hospital_H001 / hospital_H002 / hospital_H003 / hospital_H004)。`comparison_baseline_id` 已弃用,不动。
- `_read_cached_overview` 仅保留 `signature` + `fingerprint` 校验,不做版本比较。

## 测试(`backend/tests/user_profile/`)

- 改写 `test_change_overview.py`:
  - `test_key_indicators_exclude_single_report_and_sort_red_first` → 改为新口径(单报告异常**应入选**;按最近异常红>黄、极差排序);
  - `test_key_indicators_count_distinct_reports_not_rows` → 该门已移除,改为断言「同报告同名重复行仍是同一条系列、被计入」且不再按报告数排除。
- 新增:
  - **子项不进 key_indicators**:同名报告里主项 + 子项(子项标准名 canonical child,如 `血小板比积（PCT）`)且子项红/黄 → key_indicators 不含子项;
  - **单报告异常入选**:仅 1 份报告出现、红/黄 → 入选且 `delta_pct is None`;
  - **上限 10**:构造 >10 条窗口内红/黄主项 → `key_indicators` 长度 == 10;`get_overview.indicator_trends` 同测长度 == 10。
- 既有 `test_service.py` 排序/过滤用例:仅在「红黄顺序」不因新增 item_name 兜底改变时保持断言;若受影响按新排序键修正(备注:红黄不同级时不受影响)。

## 不做 / 保留

- 取窗不对齐(第 4 条决策)。
- `build_change_prompt` 的 `[:8]` 不改。
- `abnormal_distribution`、`reports[]` 头部、AI 四段总结结构不变。
- 不改表结构 / DDL / 迁移;`/tmp` 复现脚本不提交。

## 文档

- 更新 `AGENTS.md` 2026-09-10 备注:「总览保留全量」改为「总览与走势同规则、同上限(仅主项 / 窗口内红黄 / 最多 10 条);取窗仍为已完成解读的最近 N 份」。

## 验证

- `cd backend && .venv/bin/python -m pytest tests/user_profile -q`
- 端到端:`get_overview` 与 `get_change_overview`(hospital_1,u_zhangsan)两处指标名集合/顺序一致(同窗口内),总览随新规则含单报告异常、不含子项。
- 重启 `:8000` backend 后手工查看「我的」页两列表一致。
