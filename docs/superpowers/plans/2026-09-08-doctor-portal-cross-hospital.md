# 医生工作台跨院查看 + 列表过滤 + 随访刷新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 doctor-portal 可切换到任意激活医院查看报告/解读/随访/统计,过滤报告列表中的失败/空壳行,并让报告详情的随访区块在页面停留期间自动刷新。

**Architecture:** 后端在 `get_current_user`(依赖注入唯一入口)新增可选请求头 `X-Hospital-Id`,对 `doctor/admin` 角色且命中 `hospital_tenant` 激活医院时覆盖 JWT 的 `hospital_id` —— 所有院级查询因此无需逐接口改造。前端 doctor-portal 在 store/axios 层注入该请求头,并在 DoctorLayout 顶部提供医院切换器;FollowupPanel 加 15s 轮询。

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy / MySQL(多租户每院一库);前端 React 18 + antd 5 + zustand + vite + TypeScript;测试 pytest + sqlite。

**Spec:** `docs/superpowers/specs/2026-09-08-doctor-portal-cross-hospital-design.md`

## Global Constraints

- 患者端隔离:`role='user'` 永远忽略 `X-Hospital-Id`(后端逻辑必须保证)。
- 覆盖回退:未知/停用医院 → 静默回退 JWT 医院,不报 4xx。
- 报告列表过滤只作用于 `user_id is None`(doctor/admin 全量视图);用户双锚定路径逻辑不动。
- 前端拦截器只在「有 token 且 role∈{doctor,admin} 且 activeHospital 非空」时加头。
- 院级查询接口(report/interpretation/followup/statistics)均取 `CurrentUser.hospital_id`,除 `get_current_user` 外后端不改其它查询入口。
- 后端测试在 `backend/` 下运行:`cd backend && .venv/bin/python -m pytest <path> -v`;sqlite BigInteger hack 参照 `tests/followup/conftest.py`。
- 前端 typecheck:`cd frontend && npm run build -w @hospital/doctor-portal`(tsc + vite build)。
- 不改 JWT 结构、不改患者端(3001)、不做多院聚合视图、不清演示脏数据。

---

### Task 1: 后端 `get_current_user` 支持 `X-Hospital-Id` 覆盖

**Files:**
- Modify: `backend/app/core/dependencies.py`
- Test: `backend/tests/core/test_hospital_override.py`

**Interfaces:**
- Produces: `get_current_user(authorization, x_hospital_id, db) -> CurrentUser`,其中 `x_hospital_id: Optional[str]` 由 FastAPI 从请求头 `X-Hospital-Id` 绑定。

- [ ] **Step 1: 写失败测试**

Create `backend/tests/core/test_hospital_override.py`:

```python
"""X-Hospital-Id 覆盖逻辑:doctor/admin 命中激活医院则覆盖;未知回退;user 忽略。"""
import pytest

from app.core.dependencies import get_current_user
from app.core.security import create_access_token


class _Row:
    def __init__(self, hid):
        self.hid = hid

    def fetchone(self):
        return (self.hid,) if self.hid else None


class _FakeDB:
    def __init__(self, active=("H001", "H002")):
        self.active = set(active)

    def execute(self, sql, params=None):
        hid = (params or {}).get("hid")
        return _Row(hid) if hid in self.active else _Row(None)


def _token(role="doctor", hospital_id="H001"):
    return create_access_token({
        "user_id": 1, "role": role, "hospital_id": hospital_id,
        "id_card_suffix": None, "name": None,
    })


async def _call(role="doctor", jwt_hospital="H001", header=None):
    return await get_current_user(
        authorization=f"Bearer {_token(role, jwt_hospital)}",
        x_hospital_id=header,
        db=_FakeDB(),
    )


@pytest.mark.asyncio
async def test_doctor_header_overrides_token_hospital():
    u = await _call(role="doctor", jwt_hospital="H001", header="H002")
    assert u.role == "doctor"
    assert u.hospital_id == "H002"


@pytest.mark.asyncio
async def test_doctor_header_unknown_falls_back_to_token():
    u = await _call(role="doctor", jwt_hospital="H001", header="Z999")
    assert u.hospital_id == "H001"


@pytest.mark.asyncio
async def test_doctor_header_inactive_hospital_falls_back():
    u = await _call(role="doctor", jwt_hospital="H001", header="H003")
    assert u.hospital_id == "H001"


@pytest.mark.asyncio
async def test_user_header_ignored():
    u = await _call(role="user", jwt_hospital="H001", header="H002")
    assert u.hospital_id == "H001"


@pytest.mark.asyncio
async def test_no_header_uses_token_hospital():
    u = await _call(role="doctor", jwt_hospital="H001")
    assert u.hospital_id == "H001"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_hospital_override.py -v`
Expected: FAIL(`get_current_user() got an unexpected keyword argument 'x_hospital_id'`)。

- [ ] **Step 3: 实现覆盖逻辑**

在 `backend/app/core/dependencies.py`:

1. 在 `get_current_user` 签名加 `x_hospital_id`:

```python
async def get_current_user(
    authorization: str = Header(..., description="Bearer <token>"),
    x_hospital_id: Optional[str] = Header(default=None),
    db: Session = Depends(get_template_db),
) -> CurrentUser:
```

2. 在解码得到 `hospital_id` 后、`set_current_hospital_id` 前插入覆盖块:

```python
    if (
        x_hospital_id
        and role in ("doctor", "admin")
        and hospital_id_active(db, x_hospital_id)
    ):
        hospital_id = x_hospital_id
    if hospital_id:
        set_current_hospital_id(hospital_id)
```

3. 文件底部加私有辅助函数:

```python
def hospital_id_active(db: Session, hospital_id: str) -> bool:
    """hospital_tenant 中 is_active=1 才算可覆盖(模板库会话只读查询)。"""
    row = db.execute(
        text("SELECT hospital_id FROM hospital_tenant "
             "WHERE hospital_id = :hid AND is_active = 1"),
        {"hid": hospital_id},
    ).fetchone()
    return row is not None
```

- [ ] **Step 4: 运行通过**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_hospital_override.py -v`
Expected: 5 passed。

- [ ] **Step 5: 提交**(提交与否遵循仓库约定/用户意愿)

```bash
git add backend/app/core/dependencies.py backend/tests/core/test_hospital_override.py
git commit -m "feat: get_current_user 支持 X-Hospital-Id 跨院覆盖(doctor/admin)"
```

---

### Task 2: `GET /api/v1/tenants` 对 doctor 开放

**Files:**
- Modify: `backend/app/modules/tenant/router.py:20`
- Test: `backend/tests/modules/tenant/test_doctor_access.py`

**Interfaces:**
- Consumes: `require_role`、`service.list_tenants(db)`(输出 `TenantListResponse{items, total}`)。
- Produces: doctor/admin 均可 `GET /api/v1/tenants` 拿到激活医院下拉数据。

- [ ] **Step 1: 写失败测试**

Create `backend/tests/modules/tenant/test_doctor_access.py`:

```python
"""医院切换器数据源:GET /tenants 允许 doctor/admin;user 仍 403。"""
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core.dependencies import get_current_user, CurrentUser
from app.core.database import get_template_db


class _Row:
    def __init__(self, hospital_id, hospital_name, is_active):
        self.hospital_id = hospital_id
        self.hospital_name = hospital_name
        self.is_active = is_active


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return None


class _FakeDB:
    def execute(self, sql, params=None):
        return _Result([
            _Row("H001", "演示医院", 1),
            _Row("1", "市人民医院", 1),
        ])


def _tpl():
    yield _FakeDB()


def _use(user):
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_template_db] = _tpl
    return app


def _teardown(app):
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_template_db, None)


def test_doctor_can_list_tenants():
    app = _use(CurrentUser(user_id=1, role="doctor", hospital_id="H001"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/tenants")
        assert r.status_code == 200
        ids = {x["hospital_id"] for x in r.json()["items"]}
        assert "H001" in ids and "1" in ids
    finally:
        _teardown(app)


def test_admin_can_list_tenants():
    app = _use(CurrentUser(user_id=3, role="admin", hospital_id="H001"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/tenants")
        assert r.status_code == 200
    finally:
        _teardown(app)


def test_user_cannot_list_tenants():
    app = _use(CurrentUser(user_id=5, role="user", hospital_id="H001",
                           id_card_suffix="123456", name="张三"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/tenants")
        assert r.status_code == 403
    finally:
        _teardown(app)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/modules/tenant/test_doctor_access.py -v`
Expected: doctor 用例 FAIL(403)。

- [ ] **Step 3: 放宽角色**

`backend/app/modules/tenant/router.py:20`:

```python
    _admin: None = Depends(require_role("admin")),
```
改为:
```python
    _staff: None = Depends(require_role("admin", "doctor")),
```

- [ ] **Step 4: 运行通过**

Run: `cd backend && .venv/bin/python -m pytest tests/modules/tenant/test_doctor_access.py -v`
Expected: 3 passed。

- [ ] **Step 5: 提交**

```bash
git add backend/app/modules/tenant/router.py backend/tests/modules/tenant/test_doctor_access.py
git commit -m "feat: GET /tenants 对 doctor 开放(医院切换器数据源)"
```

---

### Task 3: `list_reports` 过滤 failed / 空壳行(doctor/admin 全量视图)

**Files:**
- Modify: `backend/app/modules/report/service.py`(`list_reports`,约 292-346 行)
- Test: `backend/tests/test_report_list_filter.py`

**Interfaces:**
- Consumes: `list_reports(db, hospital_id, user_id, name, page, page_size)`。
- Produces: `user_id is None` 时返回排除 `task_status='failed'` 与「parsed_name/name/date 全 NULL」后的 `(items, total)`;患者路径不变。

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_report_list_filter.py`:

```python
"""doctor/admin 全量报告列表应排除 failed 与空壳残留行(白行来源);患者路径不受影响。"""
from datetime import date

import pytest
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportTask, ReportInfo  # noqa: F401


def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_int(ReportTask.__table__.c.id, ReportInfo.__table__.c.id)
    Base.metadata.create_all(engine)
    _restore(saved)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed(db):
    def task(status):
        t = ReportTask(user_id="2", original_file_path="/tmp/x.pdf",
                       original_filename="x.pdf", file_type="pdf",
                       file_size=1, status=status)
        db.add(t)
        db.flush()
        return t.id

    # (a) 失败任务 + 全空
    tid = task("failed")
    db.add(ReportInfo(task_id=tid, user_id="2"))
    # (b) completed 但全空壳
    tid = task("completed")
    db.add(ReportInfo(task_id=tid, user_id="2"))
    # (c) 正常行
    tid = task("completed")
    db.add(ReportInfo(task_id=tid, user_id="011234", name="张三",
                      parsed_name="张子冉", gender="男", age=29,
                      report_date=date(2025, 6, 20)))
    # (d) 无 task 但有数据(存量,应保留)
    db.add(ReportInfo(user_id="2", name="李四", gender="女",
                      report_date=date(2025, 1, 1)))
    db.commit()


def test_doctor_list_hides_failed_and_blank_shells(db):
    from app.modules.report.service import list_reports
    _seed(db)
    items, total = list_reports(db, "H001", None, None, 1, 20)
    assert total == 2  # (c) + (d)
    names = {i["name"] for i in items}
    assert "张子冉" in names and "李四" in names


def test_user_anchor_list_unaffected(db):
    from app.modules.report.service import list_reports
    _seed(db)
    items, total = list_reports(db, "H001", "011234", "张三", 1, 20)
    assert total == 1
    assert items[0]["name"] == "张子冉"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_list_filter.py -v`
Expected: `test_doctor_list_hides_failed_and_blank_shells` FAIL(total==4 而非 2)。

- [ ] **Step 3: 实现过滤**

`backend/app/modules/report/service.py`:
1. 模块导入区加 `from sqlalchemy import or_`(放在现有 `from sqlalchemy.orm import Session` 之后)。
2. `list_reports` 中,现有 `if user_id:` 块之后插入:

```python
    if not user_id:
        # 医生/管理员全量视图:隐藏 parse 失败行与「无任何可展示内容」的空壳残留行。
        q = (
            q.outerjoin(ReportTask, ReportTask.id == ReportInfo.task_id)
            .filter(
                or_(ReportTask.status.is_(None), ReportTask.status != "failed"),
                or_(ReportInfo.parsed_name.isnot(None),
                    ReportInfo.name.isnot(None),
                    ReportInfo.report_date.isnot(None)),
            )
        )
```

- [ ] **Step 4: 运行通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_list_filter.py -v`
Expected: 2 passed。

- [ ] **Step 5: 回归(相关既有测试)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_parsed_name.py -v`
Expected: 全绿(展示名/归属语义不受影响)。

- [ ] **Step 6: 提交**

```bash
git add backend/app/modules/report/service.py backend/tests/test_report_list_filter.py
git commit -m "feat: 医生/管理员报告列表过滤 failed 与空壳残留行"
```

---

### Task 4: doctor-portal store:activeHospital + axios 自动带头

**Files:**
- Modify: `frontend/packages/doctor-portal/src/stores/doctorStore.ts`

**Interfaces:**
- Produces: store 状态 `activeHospital`、`hospitals`、方法 `loadHospitals()`、`setHospital(id)`;`api` 请求在 doctor/admin 且已登录时自动带 `X-Hospital-Id`。

- [ ] **Step 1: 修改 store**

在 `frontend/packages/doctor-portal/src/stores/doctorStore.ts`:

1. 顶部加常量:

```ts
const getActiveHospital = () => localStorage.getItem('doctor_active_hospital') || null;
```

2. 接口扩展:

```ts
interface HospitalOption { hospital_id: string; hospital_name: string; }

interface DoctorState {
  token: string | null; userId: number | null; role: string; hospitalId: string | null;
  api: ReturnType<typeof createApiClient>;
  hospitalName: string;
  sidebarCollapsed: boolean;
  activeHospital: string | null;
  hospitals: HospitalOption[];
  setAuth: (token: string, userId: number, role: string, hospitalId: string) => void;
  setHospital: (id: string) => void;
  loadHospitals: () => Promise<void>;
  logout: () => void;
  toggleSidebar: () => void;
}
```

3. 创建 store 改为 `(set, get)`,新增状态:

```ts
export const useDoctorStore = create<DoctorState>((set, get) => ({
  token: getToken(),
  userId: getUserId(),
  role: getRole(),
  hospitalId: getHospitalId(),
  api: createApiClient(getToken),
  hospitalName: '',
  sidebarCollapsed: false,
  activeHospital: getActiveHospital() || getHospitalId(),
  hospitals: [],
  ...
```

4. 在 `api` 创建后追加拦截器(紧跟在 `api: createApiClient(getToken),` 所在对象体内不行 —— 拦截器需在对象外对实例操作,故改为构造后再 create?创建顺序问题:原实现是对象字面量直接赋值 `api`。调整为先造实例再组装):

```ts
const apiClient = createApiClient(getToken);
apiClient.interceptors.request.use((config) => {
  const token = getToken();
  const st = useDoctorStore.getState();
  if (token && (st.role === 'doctor' || st.role === 'admin') && st.activeHospital) {
    config.headers['X-Hospital-Id'] = st.activeHospital;
  }
  return config;
});

export const useDoctorStore = create<DoctorState>((set, get) => ({
  ...
  api: apiClient,
  ...
  setAuth: (token, userId, role, hospitalId) => {
    localStorage.setItem('doctor_token', token);
    localStorage.setItem('doctor_role', role);
    localStorage.setItem('doctor_user_id', String(userId));
    localStorage.setItem('doctor_hospital_id', hospitalId);
    localStorage.setItem('doctor_active_hospital', hospitalId);
    set({ token, userId, role, hospitalId, activeHospital: hospitalId });
  },
  setHospital: (id) => {
    localStorage.setItem('doctor_active_hospital', id);
    set({ activeHospital: id });
  },
  loadHospitals: async () => {
    const { api, token } = get();
    if (!token) return;
    try {
      const r = await api.get('/tenants');
      set({ hospitals: (r.data?.items || []).map((x: any) => ({
        hospital_id: x.hospital_id, hospital_name: x.hospital_name || x.hospital_id,
      })) });
    } catch { /* 401 由拦截器处理;网络失败保持空,不阻断页面 */ }
  },
  logout: () => {
    localStorage.removeItem('doctor_token');
    localStorage.removeItem('doctor_role');
    localStorage.removeItem('doctor_user_id');
    localStorage.removeItem('doctor_hospital_id');
    localStorage.removeItem('doctor_active_hospital');
    set({ token: null, userId: null, role: '', hospitalId: null,
          activeHospital: null, hospitals: [], hospitalName: '' });
  },
  ...
```

> 注:`get()` 需在 `create` 回调参数处改为 `(set, get)`。`loadHospitals` 因请求带头被后端忽略,/tenants 不需医院上下文,安全。

- [ ] **Step 2: typecheck**

Run: `cd frontend && npm run build -w @hospital/doctor-portal`
Expected: tsc 无错、vite build 成功。

- [ ] **Step 3: 提交**

```bash
git add frontend/packages/doctor-portal/src/stores/doctorStore.ts
git commit -m "feat(doctor-portal): activeHospital 状态 + axios 自动带 X-Hospital-Id"
```

---

### Task 5: DoctorLayout 医院切换器 UI

**Files:**
- Modify: `frontend/packages/doctor-portal/src/components/DoctorLayout.tsx`

**Interfaces:**
- Consumes: `useDoctorStore` 的 `activeHospital/hospitals/loadHospitals/setHospital/hospitalId`。
- Produces: Header 显示当前医院名 + 医院 Select;切换即持久化并重载当前路由。

- [ ] **Step 1: 修改布局**

在 `frontend/packages/doctor-portal/src/components/DoctorLayout.tsx`:

1. imports 加 `Select` 与 hooks:

```tsx
import { ReactNode, useEffect } from 'react';
import { Select } from 'antd';
...
import { useDoctorStore } from '../stores/doctorStore';
```

2. 组件内读取状态:

```tsx
export default function DoctorLayout({ children }: { children: ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const { logout, sidebarCollapsed, toggleSidebar, role, activeHospital,
          hospitals, loadHospitals, setHospital } = useDoctorStore();

  useEffect(() => { if (hospitals.length === 0) loadHospitals(); /* eslint-disable-next-line */ }, []);

  const curName = hospitals.find(h => h.hospital_id === activeHospital)?.hospital_name || activeHospital || '';
```

3. header 右侧(在 `☰` 与当前菜单 label 之后)加:

```tsx
        <span style={{ fontSize: 14, color: 'var(--color-text-secondary)', flex: 1 }}>
          {MENU.find(m => m.key === loc.pathname || (m.key !== '/' && loc.pathname.startsWith(m.key)))?.label || ''}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {curName && <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>🏥 {curName}</span>}
          {hospitals.length > 1 && (
            <Select
              size="small"
              value={activeHospital || undefined}
              style={{ width: 160 }}
              options={hospitals.map(h => ({
                value: h.hospital_id,
                label: `${h.hospital_name}(${h.hospital_id})`,
              }))}
              onChange={(v) => { setHospital(v); window.location.reload(); }}
            />
          )}
        </div>
```

> 切换后 `window.location.reload()`:让所有已发出的查询与路由重挂载都落到新库(行为可预期、最简单)。

- [ ] **Step 2: typecheck**

Run: `cd frontend && npm run build -w @hospital/doctor-portal`
Expected: 无错、build 成功。

- [ ] **Step 3: 提交**

```bash
git add frontend/packages/doctor-portal/src/components/DoctorLayout.tsx
git commit -m "feat(doctor-portal): 顶部医院切换器 + 当前医院标识"
```

---

### Task 6: FollowupPanel 15s 轮询

**Files:**
- Modify: `frontend/packages/doctor-portal/src/components/FollowupPanel.tsx`

**Interfaces:**
- Consumes: `props.reportId`, store `api`。
- Produces: 页面停留期间每 15s 重取 `/followup/by-report/{reportId}`,患者后填答案/状态自动出现。

- [ ] **Step 1: 修改组件**

把 `FollowupPanel.tsx` 的 `useEffect`(现 20-24 行)替换为:

```tsx
  useEffect(() => {
    let stopped = false;
    let first = true;
    const load = async () => {
      try {
        const r = await api.get(`/followup/by-report/${reportId}`);
        if (!stopped) setData(r.data);
      } catch {
        // 404 或无记录:保留已有数据;从未成功则保持 null(不占版面)
      } finally {
        if (!stopped && first) { first = false; setLoaded(true); }
      }
    };
    load();
    const timer = setInterval(load, 15000);
    return () => { stopped = true; clearInterval(timer); };
  }, [reportId, api]);
```

- [ ] **Step 2: typecheck**

Run: `cd frontend && npm run build -w @hospital/doctor-portal`
Expected: 无错、build 成功。

- [ ] **Step 3: 提交**

```bash
git add frontend/packages/doctor-portal/src/components/FollowupPanel.tsx
git commit -m "feat(doctor-portal): 随访问卷面板 15s 轮询自动刷新"
```

---

### Task 7: AGENTS.md 契约增补 + 全量验证

**Files:**
- Modify: `AGENTS.md`
- 验证(不产出代码)

**Interfaces:**
- 产出:AGENTS.md 新节「医生工作台跨院查看(X-Hospital-Id)」。

- [ ] **Step 1: AGENTS.md 增补**

在 `AGENTS.md` 末尾追加一节(内容用中文,与全文风格一致):

```markdown
## 医生工作台跨院查看(X-Hospital-Id)(2026-09-08 起)

**事实**: doctor-portal 是单医院视图,靠 `get_current_user` 从 JWT 取 `hospital_id` 选库。
2026-09-08 起支持**请求头 `X-Hospital-Id` 覆盖**:

- 契约:doctor/admin 角色带 `X-Hospital-Id: <hospital_id>` 且该院在 `hospital_tenant.is_active=1`
  时,后端用请求头医院覆盖 JWT 医院(一处改动在 `dependencies.py::get_current_user`,
  报告/解读/高危/随访/统计等院级查询自动跟随);未知/停用医院静默回退 JWT 医院;
  `role='user'`(患者端/App)一律忽略该头,绝不跨院。
- 医生端(`doctor-portal`)Header 顶部有医院切换器:选项来自 `GET /api/v1/tenants`
  (该接口已对 doctor 开放),选择后写 `localStorage['doctor_active_hospital']` 并经
  axios 拦截器自动带头,页面重载切库。患者端(3001)不发该头。
- 报告管理列表(doctor/admin 全量视图)过滤 `task_status='failed'` 与
  `parsed_name/name/report_date 全 NULL` 的空壳残留行(演示库白行来源);
  患者按锚点查询不受影响。
- 历史演示库(hospital_H001,3056 条)含大量空壳/失败残留行,列表已隐藏,未批量清理。
```

- [ ] **Step 2: 后端全量测试**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 全绿(新增 3 个测试文件用例含在内)。

- [ ] **Step 3: 前端全量 typecheck**

Run: `cd frontend && npm run build -w @hospital/shared && npm run build -w @hospital/doctor-portal`
Expected: 成功。

- [ ] **Step 4: 手工验证(需 backend + front 已在跑)**

1. 重启 backend 使改动生效(`bash start.sh --stop && bash start.sh`,或按部署习惯重启 uvicorn)。
2. doctor1/123456 登录 `http://localhost:3002`,确认 Header 出现「🏥 演示医院」与切换器。
3. 切到「市人民医院(1)」→ 页面重载 → 报告管理应只有 ~26 条、无白行,点最新「张子冉」
   (report 27)详情页底部应出现「随访问卷」卡片与 10 题答案。
4. 保持详情页打开,在 `http://localhost:3001` 用 u_zhangsan/123456 把一份 pending 随访改答并提交,
   回到医生详情页等 ≤15s,随访问卷状态应自动变「已填写」并显示新答案。
5. 用 user1/123456 登录 3001 患者端,确认报告/随访仍只显示本院数据(拦截器未加头行为不变)。
6. 切回「演示医院(H001)」,确认报告管理回到 3056 全量但无白行/失败行。

- [ ] **Step 5: 提交**

```bash
git add AGENTS.md
git commit -m "docs: AGENTS.md 增补 X-Hospital-Id 跨院契约"
```
