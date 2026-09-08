# 医生工作台:医院切换器 + 列表过滤脏行 + 随访面板实时刷新 设计

**日期**:2026-09-08
**状态**:Draft(需求与设计决策已与用户对齐,待 review)
**前置**:
- 多租户架构(每医院一个库 `hospital_<id>`): `docs/superpowers/specs/2026-07-17-tenant-creation-endpoint-design.md`
- 身份证后六位双锚定与展示名/归属分离: `docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md`
- 检后随访(医生端报告详情随访区块): `docs/superpowers/specs/2026-09-07-followup-questionnaire-and-reminder-design.md`
- 日志收口与 logger 命名: `AGENTS.md`

---

## 0. 目标与边界

### 背景(真实故障)
- 测试环境存在两家独立医院租户:**H001/演示医院**(`hospital_H001`,约 3056 条旧演示报告)与
  **`1`/市人民医院**(`hospital_1`,2026-09-02 起的批量上传/随访主场景)。
- 随访场景:患者 `u_zhangsan/张三`(hospital_1)完成某份红/黄报告(市人民医院 report 27)的随访问卷;
  医生用 `admin1`(hospital H001)登录医生工作台,在「报告管理」点开 H001 里**同名不同人**的张子冉,
  看不到刚填的随访结果。
- 医生工作台(doctor-portal)所有数据接口都只查 `current_user.hospital_id` 对应单库
  (`report/router.py:_get_db`、`followup/router.py:_get_db`、interpretation/statistics 同理),
  前端也无任何医院维度/标识 → **医生无法看其它医院结果**。

### 目标
1. 医生工作台支持**在所有激活医院间切换**,切换后报告/解读/高危/随访/统计等全部随之切到所选医院库。
2. 医生/管理员的「报告管理」列表**不再显示**空壳残留行(整行 NULL)与 parse 失败行。
3. 医生停留在报告详情页时,若患者后补填了随访问卷,随访区块**自动刷新**(无需手动刷新页面)。

### 使用方与权限(硬约束)
- **患者端(user-portal / 外部 App)绝不跨院**:`role='user'` 的上下文永远取 JWT,不允许任何覆盖。
- **医生工作台登录方**均为 `role ∈ {doctor, admin}`(平台/院端运营账号)。按用户确认:
  所有 doctor/admin **都能**切换到任意激活医院(不区分院级/平台级)。
- 切换只影响**该医生工作台当前会话/浏览器**(localStorage + 请求头),不改 JWT、不改用户归属。

### 范围内
- 后端 `X-Hospital-Id` 请求头覆盖机制(`get_current_user` 一处改动,覆盖所有院级接口)
- `GET /api/v1/tenants` 对 doctor 开放(切换器下拉数据源)
- 报告列表过滤脏行(doctor/admin 全量视图)
- doctor-portal 顶部医院切换器 + 当前医院标识
- 随访面板 15s 轮询
- 测试(后端)+ `AGENTS.md` 增补请求头契约

### 范围外(YAGNI)
- **不清库**:H001 旧演示库 3056 条脏数据不批量删除/迁移(演示数据,需另走人工清理;本次仅列表层面隐藏)
- 不改患者端、不改 JWT 有效期/结构、不加真推送
- 不做「默认聚合多院一屏」视图(用户已选切换器形态)
- 统计报表等页面的跨院聚合不做(切换器已满足跨院查看)

---

## 1. 决策汇总(与用户确认)

| 维度 | 决策 |
|------|------|
| 形态 | 医生工作台加**医院切换器**(保留单院视图,切库查看),非默认聚合 |
| 谁能切 | `role ∈ {doctor, admin}` 均可切到任意 `hospital_tenant.is_active=1` 的医院 |
| 患者隔离 | `role='user'` 忽略任何跨院请求头 |
| 覆盖通道 | 请求头 `X-Hospital-Id`(医生端 axios 请求拦截器自动带),后端 `get_current_user` 校验后覆盖 JWT 的 hospital_id |
| 白行定义 | `parsed_name/name 全 NULL 且 report_date IS NULL` 的空壳行;或 `task_status='failed'` 的失败行 |
| 过滤范围 | 仅 doctor/admin 的全量列表(`user_id is None`);患者按锚点查询不受影响 |
| 随访刷新 | `FollowupPanel` 每 15s 重取(页面停留期间自动看到新答案/状态) |

---

## 2. 后端改动

### 2.1 `app/core/dependencies.py`:`get_current_user` 支持 `X-Hospital-Id`

```python
async def get_current_user(
    authorization: str = Header(..., description="Bearer <token>"),
    x_hospital_id: Optional[str] = Header(default=None),   # 新增
    db: Session = Depends(get_template_db),
) -> CurrentUser:
```

- 解码出 `role/hospital_id` 后,若:
  - `x_hospital_id` 非空,
  - `role ∈ {"doctor", "admin"}`,
  - 且该 id 命中 `hospital_tenant`(`hospital_id = :hid AND is_active = 1`),
  则 `hospital_id = x_hospital_id`,并照常 `set_current_hospital_id(hospital_id)`。
- 否则一律用 JWT 里的 `hospital_id`(不抛错、静默回退)。`role='user'` 即使带该头也忽略。
- 命中校验复用已在依赖里的 `get_template_db` 会话,单条 `SELECT hospital_id FROM hospital_tenant ...`。

### 2.2 `app/modules/tenant/router.py`:`GET /tenants` 对 doctor 开放
- `list_tenants` 的 `require_role("admin")` → `require_role("admin", "doctor")`。只读、低敏感。

### 2.3 `app/modules/report/service.py`:`list_reports` 过滤脏行
- 仅当入参 `user_id is None`(doctor/admin 全量视图)时追加 SQL 排除:
  - `ReportTask.status == "failed"` 的行(LEFT JOIN `report_task`,保持每 report 一条);
  - `ReportInfo.parsed_name IS NULL AND ReportInfo.name IS NULL AND ReportInfo.report_date IS NULL` 的空壳行。
- `total` 用过滤后的计数,`order_by/分页` 基于过滤后查询,保证翻页一致。
- `user_id` 非空(患者路径)保持原逻辑不动。

---

## 3. doctor-portal 改动

### 3.1 `doctorStore.ts`
- 新状态:
  - `activeHospital: string | null` — 初始取 `localStorage['doctor_active_hospital']`,无则回退登录 `hospitalId`。
  - `hospitals: {hospital_id, hospital_name}[]` — 切换器选项。
  - `loadHospitals()` — `GET /tenants`(active only)。
  - `setHospital(id)` — 写 localStorage + set;调用方随后重载当前路由。
- `api` 追加请求拦截器:**仅当存在 token**(未登录不发,避免登录页/登出后误带残留头)且
  `role ∈ {doctor, admin}` 且 `activeHospital` 非空时,给每个请求加
  `X-Hospital-Id: activeHospital`(读 `getState()` 保证取到最新值)。
  `role='user'` 侧(本 portal 不存在)不受影响。

### 3.2 `DoctorLayout.tsx`:顶部医院切换器
- Header 右侧(或菜单标签旁)放:`🏥 {hospital_name}` + 一个 Select(选项来自 `/tenants`,显示 `hospital_name(hospital_id)`)。
- 进组件挂载时 `loadHospitals()`;未登录/加载失败不阻断页面。
- `onChange(hid)` → `setHospital(hid)` → 重载当前路由(让所有已发起的查询都落到新库)。
  实现取最稳妥的 `window.location.reload()`(路由/页面复用同一 store,重载后列表自动新库)。
- 未选时 Select 值 = `activeHospital`(默认登录医院);保证医生一进来就明确知道自己看的是哪家。

### 3.3 `FollowupPanel.tsx`:15s 轮询
- `useEffect` 内加 `setInterval(refetch, 15000)`,卸载清理;初始 `api.get(/followup/by-report/{reportId})` 逻辑保留。
- 该报告无随访(404)仍整卡不渲染;轮询在「有随访」时才需要,但统一每 15s 拉一次即可(开销小)。

---

## 4. 接口契约变化

| 接口/头 | 变更 |
|------|------|
| 请求头 `X-Hospital-Id`(新增) | doctor/admin 可选;覆盖院级查询上下文;user 忽略;未知/停用医院忽略回退 JWT |
| `GET /api/v1/tenants` | role 要求 `admin` → `admin, doctor` |
| `GET /api/v1/reports`(doctor/admin) | 返回排除 failed/空壳行后的列表与 total |

> 注:所有院级查询(报告/详情/解读/高危/随访 by-report/统计)无需各自改动,
> 因均取 `CurrentUser.hospital_id` 选库,覆盖在 `get_current_user` 一次完成。

---

## 5. 错误处理与边界

- 医生把浏览器 activeHospital 指向已被停用/删除的医院:后端校验不通过 → 静默回退 JWT 医院。
  前端 `/tenants` 只列 active,正常不会发生;发生则以 token 医院为准,不报 4xx。
- 医生未选择(activeHospital 为空):请求不带 `X-Hospital-Id`,行为 == 现状(token 医院)。
- 随访面板轮询遇 401:沿用 axios 拦截器跳登录;网络抖动失败保留旧数据(不闪空)。

---

## 6. 测试

跟随仓库既有 sqlite/TestClient 风格(参考 `tests/followup`、`tests/report`)。

1. **`X-Hospital-Id` 覆盖**:
   - doctor token + 头=H001 → `CurrentUser.hospital_id == "H001"`;
   - 头=不存在的医院 → 回退 token 医院;
   - user token + 头 → 仍 token 医院;
   - 不带头 → token 医院。
2. **`GET /tenants`**:doctor token 可访问(200 且含医院列表);user 403。
3. **`list_reports` 过滤**:造库含 (a) failed 行、(b) completed 但 parsed_name/name/date 全 NULL 空壳、
   (c) 正常行 → `user_id=None` 时仅返回 (c),total=1;`user_id` 非空时不受影响。
4. **回归**:现有 `tests/` 全量跑(重点 `tests/followup`、`tests/user_profile`、解析/解读 batch 相关)。

前端切换器/轮询无单测框架,人工验证步骤写入实现计划。

---

## 7. 文档

- `AGENTS.md` 增补一段「医生工作台跨院查看(X-Hospital-Id)」:契约、安全边界(仅 doctor/admin、
  患者不可跨院)、与旧「院级单库查询」的关系。
