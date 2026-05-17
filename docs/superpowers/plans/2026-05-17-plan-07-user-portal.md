# 用户端前端 — 实现计划

> **Goal:** 实现用户端完整前端——首页报告列表、上传报告、报告详情、指标详情、个人中心。

**Aesthetic:** 现代私立诊所 — 暖白底 + 深青主色 + 三色预警色彩体系。DM Sans 字体。

**Tech Stack:** React 18, Ant Design 5, React Router 6, Zustand, Axios

**Branch:** `feat/user-portal` from `infra-setup`

---

## 文件结构

```
frontend/packages/user-portal/src/
├── main.tsx                    # 已有
├── App.tsx                     # 已有（更新：添加 Zustand + AuthProvider）
├── router.tsx                  # 更新：完整路由
├── stores/
│   └── userStore.ts            # 认证 + 用户状态
├── hooks/
│   └── useApi.ts               # Axios 实例 hook
├── pages/
│   ├── LoginPage.tsx           # 登录页
│   ├── HomePage.tsx            # 报告列表（更新）
│   ├── UploadPage.tsx          # 上传报告
│   ├── ReportDetailPage.tsx    # 报告详情 + 解读
│   └── ProfilePage.tsx         # 个人中心
├── components/
│   ├── Layout.tsx              # 全局布局
│   ├── ReportCard.tsx          # 报告卡片
│   ├── ColorBadge.tsx          # 三色标签
│   ├── StatusTag.tsx           # 任务状态标签
│   ├── IndicatorRow.tsx        # 指标行
│   └── TrendMiniChart.tsx      # 迷你趋势图
└── styles/
    └── global.css              # 全局样式 + CSS 变量
```

---

### Task 1: 全局样式 + 状态管理

- [ ] **Step 1: 创建分支**

```bash
git checkout infra-setup && git checkout -b feat/user-portal
mkdir -p frontend/packages/user-portal/src/{stores,hooks,pages,components,styles}
```

- [ ] **Step 2: 全局 CSS 变量**

`frontend/packages/user-portal/src/styles/global.css`:
```css
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&display=swap');

:root {
  --color-primary: #0D9488;
  --color-primary-light: #CCFBF1;
  --color-primary-dark: #0F766E;
  --color-amber: #F59E0B;
  --color-amber-light: #FEF3C7;
  --color-red: #DC2626;
  --color-red-light: #FEE2E2;
  --color-yellow: #F59E0B;
  --color-yellow-light: #FEF3C7;
  --color-green: #16A34A;
  --color-green-light: #DCFCE7;
  --color-bg: #FAFAFA;
  --color-surface: #FFFFFF;
  --color-text: #1C1917;
  --color-text-secondary: #78716C;
  --color-border: #E7E5E4;
  --color-border-light: #F5F5F4;
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
  --shadow-md: 0 4px 16px rgba(0,0,0,0.06);
  --shadow-lg: 0 8px 32px rgba(0,0,0,0.08);
  --font-body: 'DM Sans', -apple-system, sans-serif;
  --font-display: 'DM Serif Display', Georgia, serif;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: var(--font-body);
  background: var(--color-bg);
  color: var(--color-text);
  -webkit-font-smoothing: antialiased;
}

h1, h2, h3 { font-family: var(--font-display); color: var(--color-text); }
```

- [ ] **Step 3: 状态管理**

`userStore.ts`:
```typescript
import { create } from 'zustand';
import { createApiClient } from '@hospital/shared';

interface UserState {
  token: string | null;
  userId: number | null;
  role: string;
  hospitalId: string | null;
  api: ReturnType<typeof createApiClient>;
  login: (token: string) => void;
  logout: () => void;
}

export const useUserStore = create<UserState>((set, get) => ({
  token: localStorage.getItem('token'),
  userId: null,
  role: '',
  hospitalId: null,
  api: createApiClient(() => get().token),
  login: (token: string) => {
    localStorage.setItem('token', token);
    set({ token });
  },
  logout: () => {
    localStorage.removeItem('token');
    set({ token: null, userId: null, role: '' });
  },
}));
```

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/user-portal/src/
git commit -m "feat(user-portal): add global styles and state management"
```

---

### Task 2: 共享组件

- [ ] **Step 1: ColorBadge, StatusTag, ReportCard, Layout**

组件代码见执行

- [ ] **Step 2: Commit**

---

### Task 3: 页面（Login, Home, Upload, ReportDetail, Profile）

- [ ] **Step 1: 各页面实现**
- [ ] **Step 2: 路由更新**
- [ ] **Step 3: Commit**

---

### Task 4: 验证 + 推送

- [ ] **Step 1: npm install + dev server 验证**
- [ ] **Step 2: Push + merge**
