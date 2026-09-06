# 用户信息开放接口文档（免登录）

> 供外部后端系统调用，两个接口均**完全免登录**（无需 token，已加入免登录白名单 `/biz/baUserOpen/**`）。
>
> 关联源码：`BaUserOpenController`（snowy-plugin-biz / baUser / controller）

## 1. 基础信息

| 项 | 值 |
| --- | --- |
| 本地服务端口 | `82` |
| 本地上下文路径 | `/snowyApi` |
| 本地 Base URL | `http://localhost:82/snowyApi` |
| 鉴权 | 无（免登录） |
| 请求方式 | `GET` |

**统一响应结构（CommonResult）**

```json
{
  "code": 200,
  "msg": "操作成功",
  "data": {}
}
```

- `code`：`200` 成功；`500` 失败（`msg` 为失败原因）。
- `data`：接口返回数据。

---

## 2. 接口一：获取用户信息分页

- **URL**：`/biz/baUserOpen/page`
- **方式**：`GET`
- **说明**：查询用户信息，关联 `ba_user_basic`（基础信息）与 `ba_organization`（机构）。排序固定为 `创建时间(ctstamp) 倒序`。

### 2.1 入参（全部为 Query 参数，均可选）

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `current` | Integer | 否 | 页码，默认 `1` |
| `size` | Integer | 否 | 每页条数，默认 `10` |
| `orgId` | Long | 否 | 生效机构ID，精确匹配 |
| `sex` | Integer | 否 | 性别：`1`男 `2`女 |
| `ageGroup` | Integer | 否 | 年龄段：`0`≤30 `1`≤40 `2`≤50 `3`>50 |
| `realName` | String | 否 | 真实姓名，模糊（包含）匹配 |
| `mobile` | String | 否 | 手机号，模糊（包含）匹配 |
| `chronicDiseaseFlag` | Integer | 否 | 慢病标识：`0`无慢病 `1`有慢病 |

> 说明：参数对象中还有 `sortField`/`sortOrder`/`searchKey`，但**当前 SQL 未使用**，传了不生效，结果始终按 `ctstamp` 倒序。

**请求示例**

```
GET http://localhost:82/snowyApi/biz/baUserOpen/page?current=1&size=10&realName=张&orgId=1000002
```

### 2.2 出参

`data` 为分页对象（MyBatis-Plus `Page<BaUserVo>`）：

```json
{
  "code": 200,
  "msg": "操作成功",
  "data": {
    "records": [
      {
        "userId": "101",
        "userNo": "1001",
        "userName": "13800000000",
        "userNick": null,
        "realName": "张三",
        "pinyin": "zhangsan",
        "email": null,
        "mobile": "13800000000",
        "userType": 1,
        "drType": null,
        "status": 1,
        "useflag": 1,
        "ctstamp": "2024-09-19 10:02:03",
        "utstamp": "2024-09-20 11:12:13",
        "sex": 1,
        "birthday": "1990-01-01",
        "age": 34,
        "ageGroup": 2,
        "orgId": 1000002,
        "orgName": "北京总站",
        "idCardLast6": "123456"
      }
    ],
    "total": 128,
    "size": 10,
    "current": 1,
    "pages": 13,
    "orders": [],
    "optimizeCountSql": true,
    "searchCount": true,
    "countId": null,
    "maxLimit": null
  }
}
```

**records 关键字段说明**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `userId` | String | 用户ID |
| `userNo` | String | 用户编号 |
| `userName` | String | 用户名（一般为手机号） |
| `realName` | String | 真实姓名 |
| `mobile` | String | 手机号 |
| `userType` | Integer | `1`公众 `2`医生用户 |
| `status` | Integer | `1`正常 `0`封号 |
| `useflag` | Integer | `0`停用 `1`使用 |
| `ctstamp` | String | 创建时间 |
| `sex` | Long | 性别 `1`男 `2`女 |
| `birthday` | String | 出生日期（`yyyy-MM-dd`） |
| `age` | Integer | 年龄 |
| `ageGroup` | Integer | 年龄段 |
| `orgId` | Long | **生效机构ID**（该用户所属医院） |
| `orgName` | String | 机构名称 |
| `idCardLast6` | String | **身份证号后六位**（不返回完整证件号） |

> `orgId`/`orgName` 为“生效机构”：若用户机构存在上级机构（`up_org_code`），则返回上级机构；否则返回机构本身。
> 分页字段：`total` 总条数、`current` 当前页、`size` 每页条数、`pages` 总页数。

---

## 3. 接口二：按姓名 + 身份证后六位模糊查询用户

- **URL**：`/biz/baUserOpen/searchUser`
- **方式**：`GET`
- **说明**：按真实姓名与身份证后六位做模糊匹配查询。两个条件**同时满足**（AND）；若两者都为空，返回空数组。最多返回 `100` 条。

### 3.1 入参（Query 参数）

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `realName` | String | 否（二选一，但建议都传） | 真实姓名，模糊（包含）匹配 |
| `idCardLast6` | String | 否 | 身份证号后六位，模糊（包含）匹配 |

**请求示例**

```
GET http://localhost:82/snowyApi/biz/baUserOpen/searchUser?realName=张&idCardLast6=123456
```

### 3.2 出参

`data` 为数组，每项包含姓名、身份证后六位、机构ID：

```json
{
  "code": 200,
  "msg": "操作成功",
  "data": [
    {
      "realName": "张三",
      "idCardLast6": "123456",
      "orgId": 1000002
    }
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `realName` | String | 真实姓名 |
| `idCardLast6` | String | 身份证号后六位 |
| `orgId` | Long | 生效机构ID（所属医院） |

---

## 4. 本地快速验证（curl）

```bash
# 分页查询：第1页，每页10条，姓名含"张"
curl "http://localhost:82/snowyApi/biz/baUserOpen/page?current=1&size=10&realName=张"

# 分页查询：按机构过滤
curl "http://localhost:82/snowyApi/biz/baUserOpen/page?orgId=1000002"

# 姓名+身份证后六位查询
curl "http://localhost:82/snowyApi/biz/baUserOpen/searchUser?realName=张&idCardLast6=123456"
```

---

## 5. 注意事项

1. **免登录**：两个接口已在 `GlobalConfigure.NO_LOGIN_PATH_ARR` 中放行，无需任何 token。
2. **身份证隐私**：两个接口均只返回**身份证后六位**，不返回完整证件号。
3. **敏感字段**：分页接口返回的 `records` 来自 `select u.*`，会包含 `password`（MD5 暗文）、`token`、`loginIp`、`createIp`、`loginTime` 等字段。由于该接口对外免登录开放，若这些字段对调用方无必要，建议改造为只返回白名单字段（需另行处理，当前未做）。
4. **匹配语义**：`realName`、`mobile`、`idCardLast6` 均为子串模糊匹配（`locate` / `LIKE %xx%`）。
5. **部署环境**：生产/测试环境地址把 `localhost:82/snowyApi` 替换为对应网关地址即可（如 `https://szbf.yiqikang.cn/snowyApi`）。
