# 第二阶段运行与API说明

适用第二阶段功能版本c1a65d1。本说明与[第一阶段说明](../运行与API说明.md)分开，原版复现见末节。

## 1. 环境与启动

Python3.11、MongoDB8.0.32已用于验收。优先复用`contemporary_db`；依赖锁仍为仓库根目录`requirements.lock.txt`，本阶段没有新增依赖。全新依赖安装尚未执行，留待最终交付检查。

```bash
conda activate contemporary_db
python -m pip check
```

先按课程约定只读诊断阿里云，实际失败后才使用本地；应用不会自动切换连接。II-W006中阿里云域名可解析，但10秒握手关闭，发生于认证前，不能据此认定密码错误或永久不可用。

检查已有`.env`，仅在不存在时从`.env.example`建立。字段为SURVEY_MONGO_URI、SURVEY_DB、SURVEY_ORIGIN和SURVEY_SECURE_COOKIE；本人数据库名称需明确，不能保留样例占位或使用他人库。已有v1业务库当前未授权迁移，不能直接用新版启动作为升级手段。

对已准备好的v2库，或明确选择的没有任何survey_集合的新库：

```bash
python scripts/init_db.py
python scripts/run.py
```

默认访问`http://127.0.0.1:8000`。Origin必须与实际协议/主机/端口完全一致。换端口时同步配置SURVEY_ORIGIN，再用uvicorn的factory方式启动。只有完全无survey_集合时才初始化完整七集合；已有任何此类集合先严格检查，缺失或未知契约拒绝，不自动修补或迁移。

`GET /health`为ready表示契约检查通过；当前实现标记II-P3/II-W005、数据库契约2。问卷文档仍可为schema1，health契约号与文档版本含义不同。

## 2. 页面操作

注册登录后，旧问卷列表、编辑、填写与结果页面保留。新增`/questions`管理本人/共享题，`/question-banks`管理个人库，`/questions/{qid}/statistics`查看授权范围内汇总。

常用题先独立创建家族和草稿，保存、定稿后选入已保存的问卷草稿；从题库选择时也须明确版本。选择题草稿可增删、上移下移选项。已定稿内容不可直接改，需派生或恢复为新草稿。当前没有一键将旧卷题目另存常用题的专用按钮，旧卷也不会自动补来源。

题主按已存在用户名共享，接收者仅获使用权。卷主在发布前按家族单独开启结果授权，默认关，发布后只能撤回不可重开。换版保留实例ID并清本题规则；重新检查整卷配置后发布。题库默认版更新不影响已选实例。

## 3. 共用请求契约

业务API前缀`/api/v1`。会话Cookie用于认证；写请求带合法Origin、`X-Survey-Request: 1`及JSON Content-Type。严格拒绝未知字段/错误类型。ObjectId以字符串传输，实例与创建键使用UUID，数字/数字边界使用Decimal字符串，时间使用带时区的ISO表示并按UTC毫秒保存。

| 状态 | 含义 |
|---|---|
| 401 | 未登录或会话无效 |
| 404 | 资源不存在或无访问权限 |
| 409 | 状态/revision/幂等内容冲突；统计范围变化要求刷新 |
| 422 | 配置、输入或业务约束非法 |
| 503 | 数据库读取或写入故障；写入可能未知，保留原输入恢复 |

列表offset从0开始，limit默认20、范围1..100。权限过滤先于分页。家族、版本和题库创建首次201，同key同最初输入恢复200、异内容409；原POST /surveys没有creation_key。管理更新通常携带revision；不得在失败后自动套用新revision重发旧内容。

## 4. 旧业务API

| 方法与路径（省略前缀） | 作用与权限 |
|---|---|
| POST /auth/register、/auth/login、/auth/logout；GET /auth/me | 注册、会话及当前用户 |
| POST/GET /surveys；GET /surveys/{sid} | 创建、本人列表、本人管理视图 |
| PUT /surveys/{sid}/draft | 卷主草稿完整保存，保留来源，来源payload不可改 |
| POST /surveys/{sid}/publish、/close | 发布、关闭；卷主，题意冻结 |
| PATCH /surveys/{sid}/deadline | 卷主修改截止，状态及revision检查 |
| GET /fill/{sid} | 登录填写视图；七个公开题字段，schema2另有汇总告知 |
| POST /fill/{sid}/evaluate、/responses | 重放校验、明确提交；原路径/键/摘要语义保留 |
| GET /surveys/{sid}/statistics | 原整卷统计，仅卷主 |
| GET /surveys/{sid}/questions/{iid}/statistics、/contents | 原实例统计与填空内容，仅卷主 |

以上路径以实际`app/main.py`为准，填写接口使用实例UUID，不接受家族ObjectId代替。

## 5. 新增题目与题库API

| 方法与路径 | 请求与结果 |
|---|---|
| POST /questions | label、creation_key；创建本人家族 |
| GET /questions | scope=owned/shared，offset/limit；可见列表 |
| GET/PATCH /questions/{qid} | 读取可见家族；PATCH仅题主，revision/label |
| PUT /questions/{qid}/sharing | 题主revision/usernames完整名单；无账号搜索；未知、重复或本人用户名422 |
| POST /questions/{qid}/versions | creation_key、operation、parent_version_id、restored_from_version_id；create必须给payload且parent/restored_from为null；derive必须给parent并省略payload；restore的parent与restored_from相同且省略payload |
| GET /questions/{qid}/versions | 题主含draft，共享者只含sealed；按version_no降序 |
| GET /questions/{qid}/versions/{vid} | 版本必须属于家族且可见，包含来源与payload |
| PUT /questions/{qid}/versions/{vid}/draft | 题主、draft、revision/payload；结构校验，返回定稿问题 |
| POST /questions/{qid}/versions/{vid}/seal | revision、完整内容校验；重试已sealed返回同版 |
| GET /questions/{qid}/usages | version_id可选、分页；题主全部最小清单，使用者仅本人 |
| POST/GET /question-banks | title/creation_key创建；本人列表 |
| GET/PUT /question-banks/{bid} | 仅本人；PUT为revision/title/entries完整集合 |
| POST /surveys/{sid}/question-imports | 卷主draft；revision、instance_id、question_id、question_version_id；不接受payload |
| GET/PUT /surveys/{sid}/questions/{iid}/source | 卷主确切来源；PUT为revision、question_id、question_version_id，question_id必须等于原家族 |
| PUT /surveys/{sid}/question-result-grants/{qid} | 卷主revision/enabled；draft可设，发布后只撤回 |

GET确切实例来源在使用权撤回后仍可读，不授予家族其他历史权限。导入同iid同source的已成功恢复先于新写状态检查，可在发布/撤权后只读恢复；同iid异来源409。本人题无需对外授权，开启请求422。

## 6. 新统计API

`GET /questions/{qid}/statistics`接受确切`version_id`，或`versions=all`；二者互斥，缺省all。顶层scope为题主`owned_and_granted`或使用者`owned_only`，groups按可见sealed版本并列，含零使用版，不返回draft或跨版总计。

每组含version_id/version_no/kind/title、included_survey_count、included_instance_count、answer_count、answered_submission_count、respondent_count。single给选项selection_count与respondent_count，multiple只给selection_count，number给average/average_display，text只计数。计数含义见[关键逻辑](关键逻辑说明.md)。

`GET /questions/{qid}/versions/{vid}/distribution`仅number，mode=exact或buckets。exact用offset/limit，items为value/count、total为不同数值数。buckets使用URL编码的JSON字符串数组参数，例如：

```text
boundaries=["0","18","30","60","121"]
```

exact不接受boundaries，buckets不接受offset/limit；未知或重复查询参数422。2..101个边界须严格递增、合法Decimal字符串；返回lower/upper/count及underflow/overflow。分页仅限制频数展示。无回答average=null，average_display为“暂无数据”；有效0显示0.00。

仅published/closed含到期卷参与，draft只在使用清单。先授权再读答卷，返回前复核全部grant revision；变化409/STATISTICS_SCOPE_CHANGED，读取故障503。最终核对后已受理结果可完成，之后撤权排除。接口不含身份、答卷ID、逐条内容/数值、时间、整卷参与量或未授权量。

## 7. 测试入口

先按课程顺序确认连接，再运行以下本机隔离测试；测试创建并清理`survey_test_<run_id>`，不用业务.env数据库。当前九个文件共235个案例，其中原八个文件222项，独立验收文件新增13项：

```bash
python -m pytest tests/test_p2.py tests/test_w007.py tests/test_w008.py tests/test_p4.py tests/test_stage2_catalog.py tests/test_stage2_management.py tests/test_stage2_statistics.py tests/test_stage2_upgrade.py tests/test_stage2_acceptance.py -o addopts= -q
```

网页脚本需要已有Node、Playwright和Chromium/Chrome。新脚本直接读取SURVEY_NODE，请设置有效的本机路径：

```bash
export SURVEY_NODE="$(command -v node)"
export SURVEY_PLAYWRIGHT_MODULE="本机已有Playwright模块绝对路径"
export SURVEY_CHROMIUM="本机已有Chromium或Chrome可执行文件绝对路径"
python scripts/test_browser.py
python scripts/test_stage2_browser.py
python scripts/test_management_browser.py
python scripts/test_statistics_browser.py
python scripts/test_acceptance_browser.py
```

路径占位须先换成实际安装路径。依赖安装方式见根目录README。按上述顺序执行，原网页脚本创建artifacts父目录；各脚本分别在隔离环境保存报告、检查重启并清理。

## 8. 原版复现与隔离升级

原v1脚本和附件未改。必须先把3645b0764fcb01fa573ebc2e4dca47c8ef6f7017隔离导出或检出，**在该原版目录**运行：

```bash
python scripts/v1_baseline.py --restore 提交文档/附件/v1-baseline.json --output artifacts/v1-restore
```

输出目录须不存在。原版严格schema/API全等不适用于直接在新版代码运行，不删除断言绕过差异。原归档为合成4用户/3卷/8答卷。

新版升级入口只向本机127.0.0.1:27017执行，run-id须8—40位小写字母/数字，与库名后缀完全相同。先准备本人隔离v1副本并停写，再使用：

```text
python scripts/upgrade_schema_v2.py --database survey_test_<run_id> --run-id <run_id> --journal <本次本地日志.json>
```

尖括号表示需替换的参数，不直接粘贴执行。保存原日志及同目录备份，续跑使用同命令；已有步骤/快照却备份丢失时必须拒绝。工具不支持直接迁移业务库，不能把这个示例改成业务库名称尝试。允许结构差异与字段保护见[系统设计](系统与数据库设计.md)。

源码入口：[main.py](../../app/main.py)、[配置](../../app/config.py)、[升级工具](../../scripts/upgrade_schema_v2.py)。
