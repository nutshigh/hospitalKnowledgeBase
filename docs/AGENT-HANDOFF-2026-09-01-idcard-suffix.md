# Agent 接力工作文档:双锚定 + app-login + baUser 接入(测试接力)

**交接日期**:2026-09-02
**交接分支**:`feat/login-change-and-interface-expose`(当前)
**交接基点**:HEAD = `7265234`
**交接状态**:三个功能(批量上传双锚定 / app-login 免密登录 / baUser resolver 接入)实现均已提交并通过逐任务审查 + 整支审查;测试基线 **297 passed / 2 pre-existing failed**。代码已就绪,**但服务器后端仍是旧进程(Aug30 启动),数据库未迁移,尚未端到端验证**。

> 本文件是给下一个接手的 Agent 的测试接力入口。先读本文件,再按需读:
> - 批量上传双锚定:`docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md`(含 §15)
> - app-login:`docs/superpowers/specs/2026-09-01-app-login-api-key-design.md`
> - baUser 接入:`docs/superpowers/specs/2026-09-01-baUser-open-api-resolver-design.md`
> - 外部接口文档:`docs/baUser-open-api.md`
> - SDD 过程记录:`.superpowers/sdd/progress.md`
> - 工程约束:`AGENTS.md`(venv/GPU/多租户/日志)

---

## 1. 已提交内容(按提交序)

| commit | 内容 |
|--------|------|
| `beb3f5e`/`3ff9d5c`/`e89776b` | 批量上传「姓名+后六位」双锚定:resolver、extract_worker、DDL、迁移 `003_user_id_suffix.sql`、auth/chat/report/user_profile/ai 全链路 |
| `9dceb04`~`58c759a` | app-login:`POST /api/v1/auth/app-login`(app_key+name+id_card_suffix→JWT)、自动注册、`APP_API_KEY`/`APP_LOGIN_TOKEN_EXPIRE_MINUTES`、健壮性修复 |
| `696cbc2`~`7265234` | baUser 接入:`resolve_hospital(name,id_suffix)` → GET `{BaseURL}{SEARCH_USER_PATH}` → orgId=hospital_id;`EXTERNAL_RESOLVER_URL` 存 BaseURL,`SEARCH_USER_PATH="/biz/baUserOpen/searchUser"` 调用处拼装 |

## 2. 关键契约速查

- 后六位正则:`[0-9]{5}[0-9X]`;文件名 `^([^_]+)_([0-9]{5}[0-9X])$`
- 双锚定:`user_id`(后六位)+ `name`(姓名);`platform_user` 唯一索引 `uq_platform_user_anchor(hospital_id,name,id_card_suffix)`
- resolver:`resolve_hospital(name, id_suffix)`;信封 `{code,msg,data}`;data 精确过滤 `realName==name AND idCardLast6==id_suffix`;`str(orgId)` 即 hospital_id;4xx→None、5xx/code!=200/坏JSON→`ResolverUnavailableError`
- app-login:key 错/resolver 无匹配→401;name 空/后六位非法→400;resolver 宕→503;三元组不存在自动注册(username=`app_<hid>_<name>_<suffix>`,随机密码)
- `failed_stage` 不可重试三类:`oversize`/`dispatch_unmatched`/`hospital_not_found`

---

## 3. 待办项(下一个 agent 接力执行,含验证)

### 3.1 必须做(否则功能不可用)

1. **存量库迁移(先于后端部署)** — 对**每个**存量 tenant 库 `hospital_<id>` 与 `hospital_template` 执行
   `backend/scripts/manual_migrations/003_user_id_suffix.sql`。顺序敏感:
   - `hospital_<id>` 库:`report_task/report_info/chat_session.user_id` MODIFY 为 VARCHAR(16),`chat_session` 加 `name`
   - `hospital_template` 库:`platform_user` 加 `id_card_suffix`+`name`+唯一索引 `uq_platform_user_anchor`
   - **务必先迁移再重启后端**,否则 login/register/报告列表 SELECT 新列会 500
   - 当前服务器数据库状态:**未迁移**(`platform_user` 仍只有 8 列,已核实)

2. **重启后端为新代码** — 当前 `:8000` 后端进程是 **Aug30 启动的旧代码**(已核实,`ps lstart`)。迁移后需重启 backend + 三个 worker(report/interpretation/extract),vLLM/BGE/Reranker/OCR 不受影响不用动。

3. **本地租户与 orgId 对齐** — baUser 真机返回 `orgId=1`(市人民医院,`idCardLast6=011234`,姓名"张三")。当前 `hospital_tenant` 只有 H001-H004。**必须**:
   - 为 baUser 中实际存在的 orgId 建租户(`POST /api/v1/tenants` 或 SQL),使 `hospital_id == str(orgId)`
   - 否则 resolver 解析出 orgId 后 `_hospital_registered` 短路 → `hospital_not_found`

4. **注册终端用户** — 外部系统(HIS)调 `POST /api/v1/auth/register`,role='user' 必填 `name`+`id_card_suffix`+`hospital_id`;重复三元组返回 400。

### 3.2 端到端验证(上线前)

5. **baUser 真机契约核对** — 已初步验证:
   ```
   GET http://localhost:8082/snowyApi/biz/baUserOpen/searchUser?realName=张三&idCardLast6= → 200
   data: [{"realName":"张三","idCardLast6":"011234","orgId":1}]
   GET .../page?current=1&size=1 → 200,records[0] 含 userId/realName/idCardLast6/orgId/orgName
   ```
   注意:searchUser **空参返回 Tomcat HTML 400**(HTTP 400,resolver 会按 4xx→None 处理,符合预期,但仅当带参查询才有意义)。建议下一步用 `realName=张三&idCardLast6=011234` 双条件再验一次。

6. **批量上传端到端** — 上传含 `张三_011234.pdf` 的 zip → extract → 落 `report_info.name=张三` / `user_id=011234` → 用户登录看到自己报告。

7. **app-login 端到端** — 配好 `APP_API_KEY` 后:`POST /api/v1/auth/app-login {app_key,name:"张三",id_card_suffix:"011234"}` → 200 token → 以 Bearer 调 `/api/v1/reports` 看到报告;错误 key→401;未注册用户自动建行。

8. **存量用户行为核对** — 存量 role='user'(无 id_card_suffix):报告列表返回空、upload 400、chat 建会话 400。确认符合预期。

9. **X 大小写** — 后六位末位为 X 的用例,确认 baUser 返回与本地正则大小写一致。

### 3.3 建议做

10. **前端命名提示** — doctor-portal `BatchUploadPage.tsx` 提示卡仍为旧三段格式 `<姓名>_<医院编号>_<用户编号>`,改为 `<姓名>_<身份证后六位>`(plan 标注"可选同步项",未做)。
11. **doctor-portal `hospital_not_found` UI** — 红色 + 禁用重试,与 `dispatch_unmatched` 一致(未做)。
12. **searchUser 空参 400 观察** — 目前 resolver 把 4xx 当 no-match;若外部接口语义需区分"参数错"与"无匹配",可在接口文档明确后调整。

---

## 4. 验证命令速查

```bash
# 全套健康
for p in 8000 8004 8002 8003 8001; do curl -s -m2 http://localhost:$p/health >/dev/null && echo ":$p UP" || echo ":$p DOWN"; done

# baUser searchUser(真机)
curl -s "http://localhost:8082/snowyApi/biz/baUserOpen/searchUser?realName=张三&idCardLast6=011234"

# 注册终端用户(带后六位)
curl -s -X POST http://localhost:8000/api/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"u1","password":"123456","role":"user","hospital_id":"1","name":"张三","id_card_suffix":"011234"}'

# app-login
curl -s -X POST http://localhost:8000/api/v1/auth/app-login -H 'Content-Type: application/json' \
  -d '{"app_key":"<APP_API_KEY>","name":"张三","id_card_suffix":"011234"}'

# 测试基线
cd backend && .venv/bin/pytest tests/ -q   # 期望 297 passed / 2 pre-existing failed
```

**测试基线说明**:297 = 276(双锚定基线)+ 9(app-login)+ 1(config)+ 4(resolver 重写增量)+ 3(final fix 新增,含 404 caplog 改造)。2 个 pre-existing failed:`test_logging_config.py::test_monthly_rollover...`(freezegun quirk)与 `test_group_sql.py::test_high_risk_list_basic`(旧 SQL 断言),与本功能无关。

---

## 5. 服务器当前环境状态(2026-09-02 核实)

- 后端 `:8000` = Aug30 旧代码;5 个服务全部 UP(:8000/8004/8002/8003/8001)
- `backend/.env`:`EXTERNAL_RESOLVER_URL=http://localhost:8082/snowyApi`(已配,端口 8082 非文档默认 82)、`EXTERNAL_RESOLVER_TIMEOUT=10`;**`APP_API_KEY` 未配置**(app-login 一律 401)
- `hospital_tenant`:`H001`/`H002`/`H003`/`H004`(均 active);`platform_user` 11 行,**无 id_card_suffix/name 列**
- 工作区 git 干净(仅 `.superpowers/sdd/task-10-report.md` 是历史 scratch,不提交)
