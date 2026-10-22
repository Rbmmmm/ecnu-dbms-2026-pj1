# 运行与 API 说明

本说明面向第一阶段源代码的安装、页面使用和 API 调用。实际业务由 Python 3.11、FastAPI、PyMongo、Jinja2 与原生 JavaScript 实现，数据库使用 MongoDB Community 8.0.32。安装及验收的实际结果另见测试证据；本说明本身不表示 P5/P6 已验收。

## 1. 安装与启动

先准备 Python 3.11 和可连接的 MongoDB。课程环境优先只读检查助教提供的阿里云地址，确认无法连接后使用本地数据库；本项目沿用已有本地 MongoDB 服务。应用不会自动切换数据库，也不会把域名解析失败解释成密码错误。配置时使用已经确认的本人数据库，避免操作其他人的数据。

在新取得的项目根目录创建独立 Python 环境：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip check
```

这里的 `python3.11` 应指向本机已有的 Python 3.11；课程已有 `contemporary_db` 时，也可以激活该环境后以 `python -m venv .venv` 创建独立环境。测试、应用与依赖版本由 `requirements.lock.txt` 固定，不需要升级其他课程环境。

新项目尚无 `.env` 时，复制 `.env.example` 为 `.env`，填写以下配置；已有配置不能直接覆盖。真实 `.env` 不提交 GitHub，不放入提交附件。

```dotenv
SURVEY_MONGO_URI=mongodb://127.0.0.1:27017/
SURVEY_DB=ecnu你的学号
SURVEY_ORIGIN=http://127.0.0.1:8000
SURVEY_SECURE_COOKIE=false
```

`SURVEY_DB` 必须替换为本人已确认的数据库名。应用集合均以 `survey_` 开头。上例用于本机 HTTP；HTTPS 环境应把 `SURVEY_SECURE_COOKIE` 设为 `true`。Origin 必须与实际浏览器地址的协议、主机、端口完全一致，`localhost` 与 `127.0.0.1` 不可混用。

```bash
python scripts/init_db.py
python scripts/run.py
```

默认打开 `http://127.0.0.1:8000`。`GET /health` 返回 200 和 `status=ready` 表示连接与数据库契约检查通过。当前功能标识是 `stage=P4, work_package=W-009, schema_version=1`；这个健康状态不是提交文档或用户终审的结论。停止应用使用 Ctrl+C，不需要停止 MongoDB。

如果 8000 端口已有服务，可将配置中的 `SURVEY_ORIGIN` 改为 `http://127.0.0.1:18000`，并从项目根目录执行：

```bash
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18000
```

此时使用 `http://127.0.0.1:18000` 访问。不要使用固定监听 8000 的 `scripts/run.py` 启动 18000 配置。初始化创建缺失集合和索引，不清空已有数据；已有契约不符时失败，需要先查明差异，不应通过删除业务库解决。

## 2. 页面操作

1. 在注册页创建账号，再登录。用户名为 3—32 个基本汉字、英文字母、数字或下划线；英文字母统一转小写。密码为 8—128 个 Unicode 码点。
2. 在“我的问卷”创建草稿，填写标题、说明、匿名与重复设置、截止时间。添加单选、多选、文本、数字题，配置选项、校验和有序跳转规则。
3. 保存草稿后修复发布问题，再发布。发布后的题意与填写设置冻结，管理页提供分享链接、截止调整、关闭和统计入口。
4. 填写者从 `/survey/{id}` 登录进入，逐题回答或明确跳过非必答题；可以返回已访问题修改，系统重新计算路径。
5. 到达结束位置后先显示确认页，再显式提交。可匿名的问卷默认实名；同一次填写返回修改会保留匿名选择。网络结果未知时保留原内容重试，避免新建一次填写。
6. 创建者查看整卷和单题统计，文本、数字内容可翻页。允许重复时成功后可以“再填一份”，新一份默认实名。

截止时间在页面统一按北京时间输入和显示。手动关闭不可重开；已到期但未关闭的问卷可以延长或清除截止。刷新填写页不保证恢复未提交输入或原提交键，尤其允许重复问卷应避免把刷新当成原提交重试。

## 3. API 共同约定

业务 API 前缀为 `/api/v1`，共 17 个操作；加上 `/health` 共 18 个应用 API 操作。`/docs` 和 `/openapi.json` 提供当前接口模型；页面、静态文件和这些文档入口不计入业务操作数量。

除注册、登录和幂等退出外，所有业务 API 都需要登录 cookie。管理、统计和内容接口进一步检查创建者身份，他人资源统一返回 404。填写接口要求登录，并且草稿不可读。匿名内容采用字段白名单，不能返回填写者 ID、提交键、摘要或会话信息。

所有 POST、PUT、PATCH、DELETE 请求都需要：

```http
Origin: http://127.0.0.1:18000
Content-Type: application/json
X-Survey-Request: 1
```

示例使用 18000，实际调用需改为配置中的 Origin。退出同样需要这些头，发送空 JSON 对象即可。浏览器自动携带同源 cookie；脚本客户端需要保存登录响应的 cookie。服务没有启用跨域访问。

ObjectId 对外为 24 位小写十六进制字符串，题目、选项和提交键使用标准 UUID 字符串。数字题答案、上下限和数字规则 operand 使用十进制字符串，例如 `"18.0"`，不能发送 JSON 数值 `18`。未知字段及不正确类型拒绝；客户端不能提交 owner、respondent、状态、路径或统计结果来替代服务端计算。

数字必须有限且能够无损转为 Decimal128；有效系数最多 34 位，并受 Decimal128 指数范围限制。超范围、不精确、NaN、Infinity、布尔值均拒绝。数值等于整数的 `"18.0"` 满足整数约束，0 是有效答案。时间为带时区的 ISO 8601 字符串或 null，例如 `"2026-10-22T23:00:00+08:00"`；服务端统一为 UTC 毫秒，截止等于受理时刻即拒收。示例日期仅说明格式，设置时必须晚于服务器当前时间。

工程上限：请求体 1 MiB，持久问卷或答卷 8 MiB；最多 100 题，每题 100 选项、50 规则；标题 200、说明 5000、题干 1000、选项 200、文本答案 10000 码点。分页 `offset` 为 0—9223372036854775807，`limit` 为 1—100，默认 20；按 `created_at`、`_id` 均降序。新增数据期间 offset 分页可能移动，不保证快照视图。

## 4. 接口清单

下表业务路径均省略 `/api/v1`，唯独 `/health` 使用完整路径。创建与草稿的配置字段为 `title, description, allow_anonymous, allow_repeat, deadline_at`；草稿另传 `revision, questions`。

| 方法与路径 | 请求 | 成功返回与主要限制 |
|---|---|---|
| GET `/health` | 无 | 200，连接及 schema/index 检查；无需登录 |
| POST `/auth/register` | `username,password` | 201 `{id,username}`；不自动登录；重名 409 |
| POST `/auth/login` | `username,password` | 200 `{id,username}`，设置会话 cookie；账号或密码错误 401 |
| GET `/auth/me` | 无 | 200 当前账号 `{id,username}` |
| POST `/auth/logout` | `{}` | 204，无响应体；会话已失效也可退出 |
| POST `/surveys` | 配置字段，可省略使用默认值 | 201 草稿管理视图，revision=1、questions=[] |
| GET `/surveys` | `offset,limit` 查询参数 | 200 `{items,total}`，仅本人问卷摘要，不含题目数组 |
| GET `/surveys/{sid}` | 无 | 200 本人完整管理视图 |
| PUT `/surveys/{sid}/draft` | 配置字段、revision、questions | 200 新管理视图和发布问题；仅 draft；旧版本 409 |
| POST `/surveys/{sid}/publish` | `revision` | 200 发布后管理视图、share_url、content_version=1；非法配置 422 |
| PATCH `/surveys/{sid}/deadline` | `revision,deadline_at` | 200 新管理视图；仅 published，未来时刻或 null |
| POST `/surveys/{sid}/close` | `revision` | 200 closed 管理视图；已关闭可返回当前结果 |
| GET `/fill/{sid}` | 无 | 200 冻结题目、设置、版本、deadline_at、accepting、already_submitted；draft 404 |
| POST `/fill/{sid}/evaluate` | `content_version,answers` | 200 重放结果，可能含题目 errors；不写答卷 |
| POST `/fill/{sid}/responses` | `content_version,submission_key,anonymous,answers` | 首次 201 收据，同内容重试 200 原收据，冲突 409 |
| GET `/surveys/{sid}/statistics` | 无 | 200 整卷次数/人数与全部题目摘要 |
| GET `/surveys/{sid}/questions/{qid}/statistics` | 无 | 200 指定题目的同口径摘要；未知题 404 |
| GET `/surveys/{sid}/questions/{qid}/contents` | `offset,limit` 查询参数 | 200 `{question_id,kind,items,total,offset,limit}`；只支持 text/number，选择题 422 |

管理视图将 `_id` 转为 `id`，移除 `owner_id`，附带 `share_url`、`publish_issues`。草稿允许业务配置暂未完成，成功保存不等于可以发布。GET `/fill` 在关闭或到期后仍可返回配置，使用 `accepting=false` 表示当前不接收；没有独立的拒收原因字段。正式提交会返回明确错误码。

收据仅含 `response_id, survey_id, accepted_at`。内容分页每条包含 `value, created_at, anonymous`；只有实名条目另含 `username`，不返回答卷 ID 或填写者账号 ID。

## 5. 完整请求示例

以下是供调用者填写的请求格式示例，不是声称已经发生的执行记录。先注册、登录，再把实际返回 ID 与 revision 用于后续请求。测试结果及真实输入输出另列于测试文档。

```http
POST /api/v1/auth/register
Origin: http://127.0.0.1:18000
Content-Type: application/json
X-Survey-Request: 1

{"username":"api_example","password":"DemoOnly_12345"}
```

登录发送同一请求体至 `/api/v1/auth/login`，保存响应 cookie。这里的密码只用于说明格式，不是项目或数据库凭据。随后创建问卷：

```json
{
  "title": "年龄调查",
  "description": "可选择匿名提交",
  "allow_anonymous": true,
  "allow_repeat": false,
  "deadline_at": null
}
```

将此 JSON POST 至 `/api/v1/surveys`。新建成功后，用返回的 `id` 代入 `{sid}`，发送下面的完整草稿至 `PUT /api/v1/surveys/{sid}/draft`：

```json
{
  "revision": 1,
  "title": "年龄调查",
  "description": "可选择匿名提交",
  "allow_anonymous": true,
  "allow_repeat": false,
  "deadline_at": null,
  "questions": [{
    "id": "00000000-0000-0000-0000-000000000001",
    "kind": "number",
    "title": "年龄",
    "required": false,
    "validation": {"min_value":"0","max_value":"120","integer_only":true},
    "options": [],
    "rules": [{"op":"gte","operand":"18","target":"END"}]
  }]
}
```

无并发修改时保存后 revision 为 2，发布请求为 `POST /api/v1/surveys/{sid}/publish`，请求体 `{"revision":2}`；一般调用应始终使用最近响应中的 revision，不自行累加猜测。

填写者登录后，GET `/api/v1/fill/{sid}` 读取配置。评估请求 POST 至 `/api/v1/fill/{sid}/evaluate`：

```json
{
  "content_version": 1,
  "answers": {"00000000-0000-0000-0000-000000000001": "18.0"}
}
```

预期结构如下；数字规范化为 `"18"`，END 只表示可以确认：

```json
{
  "path": ["00000000-0000-0000-0000-000000000001"],
  "active_answers": {"00000000-0000-0000-0000-000000000001": "18"},
  "pending_answers": {},
  "removed_question_ids": [],
  "current_question_id": null,
  "ready_to_submit": true,
  "errors": []
}
```

确认后 POST 至 `/api/v1/fill/{sid}/responses`：

```json
{
  "content_version": 1,
  "submission_key": "00000000-0000-0000-0000-000000000099",
  "anonymous": true,
  "answers": {"00000000-0000-0000-0000-000000000001": "18"}
}
```

实际客户端每次有意新填写生成新的随机 UUID；结果未知时保留原键与原内容重试。非必答题明确空答写成 `{"题目ID":null}`，不能直接缺少此 key。多选值为选项 ID 数组，文本值为字符串；后台只把最终路径的非空答案写入 MongoDB。

### 错误响应

```json
{
  "error": {
    "code": "INVALID_ANSWERS",
    "message": "请完成当前路径的题目",
    "fields": [{"question_id":"题目ID","field":"answer","message":"本题必须回答"}]
  }
}
```

上例是错误结构说明。400 表示 JSON 格式或请求内容类型错误；401 表示登录失败或会话失效；403 表示来源校验失败；404 表示资源不存在或无管理权限；409 表示版本、状态、提交键或重复冲突；413 表示请求或文档超限；422 表示配置、类型或答案不合法；503 表示数据库不可用或契约未就绪。错误不回显密码、Mongo URI 或堆栈。

常见业务码包括 `REVISION_OR_STATE_CONFLICT`、`INVALID_CONFIGURATION`、`STALE_BRANCH_ANSWERS`、`KEY_REUSED_WITH_DIFFERENT_CONTENT`、`ALREADY_SUBMITTED`、`NOT_ACCEPTING`。evaluate 的结构合法请求可用 200 返回题目级 `errors`，调用者仍须检查 `ready_to_submit` 和 `errors`，不能仅凭 HTTP 200 显示可提交。

## 6. 回归与基线入口

完整源代码仓库中执行 P4 功能回归：

```bash
python scripts/verify.py --stage P4 --node /实际/node路径 --playwright-module /实际/playwright模块路径 --chromium /实际/Chromium路径
```

浏览器路径按实际安装填写，也可设置 `SURVEY_NODE`、`SURVEY_PLAYWRIGHT_MODULE`、`SURVEY_CHROMIUM`。Node、Playwright 与 Chromium 是浏览器验收工具，不是应用运行依赖；不可把缺失浏览器步骤标成通过。

v1 样本与恢复验证使用独立入口：

```bash
python scripts/v1_baseline.py --output artifacts/v1-check
python scripts/v1_baseline.py --restore 提交文档/附件/v1-baseline.json --output artifacts/v1-restore
python scripts/verify.py --stage P6 --node /实际/node路径 --playwright-module /实际/playwright模块路径 --chromium /实际/Chromium路径
```

这些入口只使用校验过的本地本轮隔离数据库，不接管业务 `.env` 指向的数据库。P6 在完整 Git 仓库内核对安装、样本、版本和交付材料；用户文字与贡献审核尚未完成时，即使技术检查通过也应返回非零并保存技术结果。真实结果以报告字段和退出码共同判断，不将预检查、测试收集或验证器反例代替完整阶段验收。

接口依据：[app/main.py](../app/main.py)、[app/schemas.py](../app/schemas.py)、[app/config.py](../app/config.py)；锁定依赖见 [requirements.lock.txt](../requirements.lock.txt)。
