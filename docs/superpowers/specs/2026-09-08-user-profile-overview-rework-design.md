# 用户端「我的」页改造:走势限流 + 移除异常分布卡片 + 走势行折叠

日期:2026-09-08

## 背景与目标

用户端(patient portal 3001)`/profile/overview` 驱动的「我的健康档案」页存在两个体验问题:

1. **指标走势把历史所有报告都纳入**,早年/解析失败/无判定的脏报告会污染趋势线;用户希望走势只看**最近几次体检**,且次数可配置。
2. **异常指标分布**整卡用户不需要展示,前端移除(后端字段保留,避免破坏接口与测试)。
3. 指标走势条目默认全部展开,页面过长;改为**每条默认收起、点开才出趋势图**,且支持多选展开便于对比。

口径确认:
- 「最近 N 份报告」按 `report_date` 取,**无日期报告垫底**(视为最旧),仅当有日期的报告不足 N 份时才计入。
- N 默认 **3**,通过环境变量/配置可覆盖(不改代码即可调)。
- 展开交互:**多选**(可同时展开多条)。

## 涉及改动点

### 1. 后端 `backend/app/config.py` + `.env`

新增:

```python
# User Profile
PROFILE_TREND_REPORT_LIMIT: int = 3  # 指标走势只纳入最近 N 份报告
```

`.env` 可选写 `PROFILE_TREND_REPORT_LIMIT=3`。沿用现有 pydantic-settings(`model_config = {"env_file": ".env", "extra": "ignore"}`)即可读取。

### 2. 后端 `backend/app/modules/user_profile/service.py::get_overview`

现状:全量 `reports` 按 `report_date` asc(无日期 → 由 `report_date` 排序,空值靠前,等价垫最旧),指标取点/走势排序都用全量。

改法:
- 保留 `reports` 全量、`report_ids`、`user_summary`(latest report、红黄绿计数)与 `abnormal_distribution`(仍聚合全量,字段不动)。
- 新增走势子集:

```python
limit = settings.PROFILE_TREND_REPORT_LIMIT
trend_reports = reports[-limit:]          # 最近 limit 份;无日期报告自然垫最旧
trend_report_ids = [r.id for r in trend_reports]
```

- 指标查询(`ReportIndicator.filter(ReportIndicator.report_id.in_(trend_report_ids))`)、judgment 匹配、`by_key` 取点、`latest_deviation`、`trend_direction`、`trends_sorted` 全部只基于 `trend_report_ids`。
- 用户报告数 ≤ N 时行为与现状一致(退化为全量),无需特判。
- 顶部红黄绿/日期范围、`abnormal_distribution` 语义不变。

导入 `settings`:service.py 顶部 `from app.config import settings`(模块读单例;测试无 `.env` 时走默认 3)。

### 3. 前端 `frontend/packages/user-portal/src/pages/ProfilePage.tsx`

- **移除「异常指标分布」卡片**(第 114-134 行区块);`OverviewResponse.abnormal_distribution` 字段与 `data.abnormal_distribution` 引用一并删除。后端响应仍含该字段,前端不消费即可。
- **走势行折叠(多选)**:
  - 新增 `expanded: Set<number>`(或 `number[]`)state,key 用指标在 `topTrends` 里的 index(与现有 `key={i}` 对齐)。
  - 每条走势渲染成可点击行:收起态显示「指标名 + ↑↓ + 最新值 + 色标」;点击 toggle 该 index 进 `expanded`。
  - 展开态额外渲染 `IndicatorTrendChart`。
  - 收起/展开行头加入 chevron(如 `▸/▾`)指示状态,视觉沿用现有卡片内联样式(不引入新 UI 库)。
  - `points.length < 2`(画不出线,chart 组件本就 `return null`)的指标行:**不可点击展开**,仍显示头行,chevron 隐藏。
- 搜索过滤 `filtered`、`topTrends = filtered.slice(0, 10)`、空态「暂无可视化指标」逻辑不变。

### 4. 测试 `backend/tests/user_profile/test_service.py`

新增用例:
- **limit 生效**:同一 user 造 5 份报告(id/日期递增、同一指标都有数值点),`get_overview` 返回 `indicator_trends[..].points` 只有最近 3 份的报告日期;更早报告的值不在点里。
- **limit 不足**:该 user 只有 2 份报告时,走势点仍为 2 个(退化为全量)。
- **无日期报告垫底**:5 份有日期 + 1 份无日期,走势应不含无日期那份。
- **abnormal_distribution 仍全量**:带异常判定的报告超出 limit 之外,`abnormal_distribution` 计数仍包含它(证明该 SQL 未误限流)。

现有用例不受影响(report ≤ 3 份,limit=3 全量含入):
- `test_get_overview_aggregates_abnormal_by_item_name_standard`(2 份)
- `test_get_overview_sorts_points_by_report_date`(2 份,注意该用例 id 与日期反向,limit 逻辑基于排序后的 `reports[-3:]`,仍全量含入、顺序断言不变)

## 明确不做 / 保留

- 点色语义(绿/黄 = 当份报告是否超参考范围)不改 —— 见 AGENTS.md「报告对比默认基线退化策略」无关联;若后续要改成「相对上次变化色」需另立项。
- `↑↓` 箭头仍是最后两点比较,不做整段趋势。
- 走势排序规则(最新红/黄优先、按极差)不改。
- `abnormal_distribution` 后端 SQL/字段保留;doctor/admin 端与 statistics 不依赖该字段,无回退面。
- 不做前端单测(项目 user-portal 无测试基建),以手工验证代替。

## 影响面核对

- 依赖方:`ProfilePage` 只读 `user_summary` / `indicator_trends` / `abnormal_distribution`;本改动不删后端字段,任何其它前端/接口不破。
- `get_overview` 签名与 router(`/overview`)不变。
- AGENTS.md 无需改动(本特性不涉及表结构/DDL/venv/启动编排)。

## 验证

- `cd backend && .venv/bin/python -m pytest tests/user_profile/test_service.py -q`
- 手工:登录患者端 →「我的健康档案」:
  1. 确认「异常指标分布」卡片消失;
  2. 走势默认全部收起;点开多条可同时展开;有 ≥2 数值点的可展开、单点的不可展开;
  3. 造一份第 4/5 次体检数据后确认走势只显示最近 3 次。

## 配置与取舍备注

- 默认 3 次的取舍:半年一体检约等于「近一年半」窗口,看趋势足够,又过滤早年脏报告。改配置即可调大/调小,无需发版代码。

---

# 追加设计(2026-09-08 同日迭代):走势仅展示红/黄指标 + 点旁数值 + 指标名完整

在已实现的「走势限流 + 移除异常分布 + 折叠」之上追加,推翻原 spec 中「走势展示所有指标」的隐含行为。

## 决策

- **展示过滤**:`indicator_trends` 只保留「最近 N 份报告窗口内」出现过红或黄判定的指标(窗口内任一次红/黄即保留;全绿或无判定 → 不出现在走势)。**不**看窗口外历史。
- **行排序**:按该指标在窗口内**最近一次异常点颜色**排序 —— 最新红 > 最新黄;同级再按窗口内数值极差大 → 小。
- **纵轴数值**:图内每个数据点上方标注该点数值(保留小数去尾零),点色(红/黄/绿)不变。
- **指标名完整展示**:行头名称不再被右侧数值压缩/截断,允许完整换行。
- 窗口(最近 N 份)与点数限制不变;`user_summary` / `abnormal_distribution` 语义不变。

## 对既有测试的影响(必改)

Task 1 新增的 4 条窗口测试所造指标**全无判定**(color=null),按新过滤会被整体剔除导致断言失败,需给窗口内指标补红/黄判定:
- `only_include_recent_n_reports`:窗口内最新报告指标补红判定;
- `keep_all_when_fewer_than_limit`:第 2 份指标补红判定;
- `exclude_null_dated_report`:窗口内(最近 3 份有日期)任一指标准补红判定;
- `abnormal_distribution_includes_outside_trend_window`:给窗口内最新报告指标补**黄**判定(同时保留旧报告红判定,验证 distribution 全量 + trend 窗口+过滤语义)。
其余既有用例(指标至少有一点红/黄,如 `aggregates_abnormal`、`sorts_points_by_report_date`)不受影响。

## 新增测试

1. 窗口内全绿 / 无判定 / 仅窗口外红 → 该指标不出现;窗口内黄 → 出现。
2. 排序:最新异常为红的指标排在最新异常为黄的指标前。

## 影响面

- 只改 `service.py::get_overview`(过滤+排序)、`IndicatorTrendChart.tsx`(点数值标注)、`ProfilePage.tsx`(行头名称完整)、后端测试。
- 前端无单测,`npx tsc --noEmit` 验证。
- `indicator_trends` 的 `latest_deviation` 字段(最新点颜色)保留含义,但**不再用于排序**(排序改用最近异常点颜色)。
