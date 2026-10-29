# MongoDB 问卷系统

这是当代数据管理系统课程的个人作业，作者任北鸣。系统使用 FastAPI、PyMongo、Jinja2 和原生 JavaScript，全部持久业务数据保存在 MongoDB。

用户可以创建和发布问卷，配置单选、多选、文本、数字题及条件跳转，也可以设置匿名、重复填写、截止时间或关闭问卷。填写者登录后回答，可以回退修改，在确认页提交；创建者查看整卷、单题统计和填空内容。

[项目报告 PDF](提交文档/第一阶段/2026_ECNU_PJ1_1_任北鸣.pdf) · [报告正文](提交文档/第一阶段/项目完成报告.md) · [系统与数据库设计](提交文档/第一阶段/系统与数据库设计.md) · [运行与 API](提交文档/第一阶段/运行与API说明.md) · [关键逻辑](提交文档/第一阶段/关键逻辑说明.md) · [测试结果](提交文档/第一阶段/测试用例与结果.md) · [AI 使用日志](提交文档/第一阶段/AI使用日志.md)

仓库：[Rbmmmm/ecnu-dbms-2026-pj1](https://github.com/Rbmmmm/ecnu-dbms-2026-pj1)。

## 第二阶段报告与运行入口

第二阶段在第一阶段原代码上增加常用题、共享、历史/恢复、个人题库、使用清单和授权跨卷统计。功能验收版本为`c1a65d1`。[第二阶段报告PDF](提交文档/第二阶段/2026_ECNU_PJ1_2_任北鸣.pdf) · [报告正文](提交文档/第二阶段/项目完成报告.md) · [配套清单](提交文档/第二阶段/提交清单.md)。水杉上传文件为`2026_ECNU_PJ1_2_任北鸣.pdf`。

新版完整运行/API/测试/隔离升级入口见[第二阶段运行说明](提交文档/第二阶段/运行与API说明.md)。新库仅在完全没有survey_集合时初始化七集合，已有库严格检查；v1不会在启动时自动迁移。当前业务库未授权迁移。下方第一阶段报告及101项测试说明保留其原版归属，不代替第二阶段235项技术证据。

## 安装与启动

先获取源码：

```bash
git clone https://github.com/Rbmmmm/ecnu-dbms-2026-pj1.git
cd ecnu-dbms-2026-pj1
```

运行需要 Python 3.11 和可连接的 MongoDB，本项目使用 MongoDB Community 8.0.32 验证。已有课程环境可用 `conda activate contemporary_db`；新建独立环境时，在项目根目录执行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip check
cp .env.example .env
```

将 `.env` 中的 `SURVEY_DB` 改为本人已确认的数据库名，并填写对应连接。已有 `.env` 时检查现有配置，避免覆盖。`SURVEY_ORIGIN` 须与访问地址的协议、主机、端口完全一致，配置字段见[运行与 API 说明](提交文档/第一阶段/运行与API说明.md)。

课程环境先只读检查阿里云，连接失败后使用本地 MongoDB。启动前须确认数据库可连接，应用不会自动切换。集合统一使用 `survey_` 前缀，与课程实验的 `users` 集合分开；实际 `.env` 不上传仓库。

```bash
python scripts/init_db.py
python scripts/run.py
```

打开[本机问卷系统](http://127.0.0.1:8000)。第二阶段应用只在完全没有survey_集合时创建完整集合与索引；已有库严格检查契约，不自动补齐或迁移。`GET /health` 返回 `status=ready` 表示连接、validator 与索引检查通过；未知存量 schema 版本在启动时拒绝。

需要其他端口时同时将 `.env` 的 `SURVEY_ORIGIN` 改为 `http://127.0.0.1:18000`，然后执行：

```bash
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18000
```

## 使用

创建者注册并登录后，创建问卷，添加题目、选项、校验与规则，保存后发布。填写者可在另一浏览器会话中注册，从分享链接登录，按路径填写并确认提交。创建者查看或刷新统计时，系统读取已保存答卷。

只有明确提交才保存答卷。匿名默认关闭，每账号默认一次；发布后题意冻结，仍可修改截止时间或关闭问卷。

单选同时显示按账号去重的人数与答卷次数，多选统计选择次数，数字均值排除空答、保留 0。返回修改时，系统删除确定跳过的答案，将尚未确定可达的后续答案暂存在当前页。未提交输入不保证在刷新后恢复；匿名不会自动清除填写者主动写入文本的姓名；不同统计查询不承诺同一快照。

## 运行测试

测试需要本机 `127.0.0.1:27017` 的真实 MongoDB，会自行创建 `survey_test_<run_id>` 隔离数据库并在结束后清理，不使用业务 `.env` 中的数据库。运行 API 与数据库测试：

```bash
python -m pytest tests/test_p2.py tests/test_w007.py tests/test_w008.py tests/test_p4.py -o addopts= -q
```

固定用例覆盖创建问卷、题目编辑、跳转、校验、提交与统计，以及权限、并发和数值边界，共 101 个参数案例。具体输入输出与执行结果见[测试用例与结果](提交文档/第一阶段/测试用例与结果.md)。网页测试还需要 Node.js、Playwright 和 Chromium；启动应用或运行 API 测试不需要浏览器工具。

尚未安装浏览器工具时，在项目根目录执行以下命令。Playwright 模块放在被 Git 忽略的 `artifacts/` 中，浏览器安装到默认用户缓存：

```bash
npm install --prefix artifacts/browser-tools --no-save playwright@1.62.1
node artifacts/browser-tools/node_modules/playwright/cli.js install chromium
export SURVEY_PLAYWRIGHT_MODULE="$PWD/artifacts/browser-tools/node_modules/playwright"
python scripts/test_browser.py
```

已有工具时，可用 `SURVEY_NODE`、`SURVEY_PLAYWRIGHT_MODULE`、`SURVEY_CHROMIUM` 指定路径。脚本在隔离数据库中启动测试服务并执行四套网页流程。

第一阶段样本的新建与恢复须在3645b0764fcb01fa573ebc2e4dca47c8ef6f7017原版代码的隔离导出/检出目录中执行；不要在新版直接运行并放宽schema/API全等断言：

```bash
python scripts/v1_baseline.py --output artifacts/v1-check
python scripts/v1_baseline.py --restore 提交文档/附件/v1-baseline.json --output artifacts/v1-restore
```

每个输出目录须尚不存在。脚本核对 4 个合成账号、3 份问卷和 8 份答卷的题意、字段、路径和统计，并检查应用重启后的读取结果，以及未知 schema 版本是否被拒绝。恢复只写入新建的隔离数据库，归档不含明文密码和会话 cookie。

## 第一阶段提交材料

本仓库提供前后端、MongoDB schema、配置、测试和课程要求的说明文档，文件对应关系见[提交清单](提交文档/第一阶段/提交清单.md)。水杉平台提交文件为 `2026_ECNU_PJ1_1_任北鸣.pdf`，仓库保存同名报告供查阅；平台上传需单独完成。
