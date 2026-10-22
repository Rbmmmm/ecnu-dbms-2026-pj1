# MongoDB 问卷系统

这是当代数据管理系统课程第一阶段的个人作业，作者任北鸣。系统使用 FastAPI、PyMongo、Jinja2 和原生 JavaScript，全部持久业务数据保存在 MongoDB。用户可以创建和发布问卷，配置单选、多选、文本、数字题及条件跳转；填写者登录后回答、回退修改并确认提交，创建者查看整卷、单题统计和填空内容。系统支持匿名选择、重复填写限制、截止设置和关闭问卷。

[项目报告 PDF](提交文档/2026_ECNU_PJ1_1_任北鸣.pdf) · [报告正文](提交文档/项目完成报告.md) · [系统与数据库设计](提交文档/系统与数据库设计.md) · [运行与 API](提交文档/运行与API说明.md) · [关键逻辑](提交文档/关键逻辑说明.md) · [测试结果](提交文档/测试用例与结果.md) · [AI 使用日志](提交文档/AI使用日志.md)

仓库：[Rbmmmm/ecnu-dbms-2026-pj1](https://github.com/Rbmmmm/ecnu-dbms-2026-pj1)。

## 安装与启动

先获取源码：

```bash
git clone https://github.com/Rbmmmm/ecnu-dbms-2026-pj1.git
cd ecnu-dbms-2026-pj1
```

需要 Python 3.11 和可连接的 MongoDB；本次验证 MongoDB Community 8.0.32。已有课程环境可用 `conda activate contemporary_db`；从干净环境复现时在项目根目录执行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip check
cp .env.example .env
```

将 `.env` 中的 `SURVEY_DB` 改为本人已确认的数据库名，并填写对应连接。已有 `.env` 时直接检查配置，避免覆盖。课程环境优先只读检查阿里云，连接失败后使用本地 MongoDB；启动前确认数据库可连接，应用不会自动切换。集合统一使用 `survey_` 前缀，与课程实验的 `users` 集合分开；实际 `.env` 不上传仓库。`SURVEY_ORIGIN` 须与访问地址的协议、主机、端口完全一致，配置字段见[运行与 API 说明](提交文档/运行与API说明.md)。

```bash
python scripts/init_db.py
python scripts/run.py
```

打开 [本机问卷系统](http://127.0.0.1:8000)。启动创建缺失集合/索引并检查已有契约，不清空业务数据，不自动迁移不兼容数据。`GET /health` 返回 `status=ready` 表示连接、validator 与索引检查通过；未知存量 schema 版本在启动时拒绝。

需要其他端口时同时将 `.env` 的 `SURVEY_ORIGIN` 改为 `http://127.0.0.1:18000`，然后执行：

```bash
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18000
```

## 使用

创建者注册、登录，创建问卷并添加题目、选项、校验与规则，保存后发布。另一浏览器会话注册填写者，从分享链接登录，按路径填写并确认提交；创建者打开统计。只有明确提交才保存答卷；匿名默认关闭，每账号默认一次。发布后题意冻结，仍可修改截止或关闭。查看/刷新统计读取已保存答卷。

单选同时显示按账号去重的人数与答卷次数；多选计选择次数；数字均值排除空答、保留 0。回退删除确定跳过的答案，尚未确定可达的后续答案只在当前页暂存。未提交输入不保证刷新恢复；匿名不自动清除填写者主动写入文本的姓名；统计不同查询不承诺同一快照。

## 运行测试

测试需要本机 `127.0.0.1:27017` 的真实 MongoDB。测试自行创建 `survey_test_<run_id>` 隔离数据库并在结束后清理，不使用业务 `.env` 中的数据库。API 与数据库测试：

```bash
python -m pytest tests/test_p2.py tests/test_w007.py tests/test_w008.py tests/test_p4.py -o addopts= -q
```

固定用例覆盖创建问卷、题目编辑、跳转、校验、提交与统计，以及权限、并发和数值边界，共 101 个参数案例。历史执行结果和具体输入输出见[测试用例与结果](提交文档/测试用例与结果.md)。网页测试另外需要 Node.js、Playwright 和 Chromium；仅启动应用或运行 API 测试不需要浏览器工具。

如未安装浏览器工具，在项目根目录执行（Playwright 模块放在被 Git 忽略的 `artifacts/` 中，浏览器安装到默认用户缓存）：

```bash
npm install --prefix artifacts/browser-tools --no-save playwright@1.62.1
node artifacts/browser-tools/node_modules/playwright/cli.js install chromium
export SURVEY_PLAYWRIGHT_MODULE="$PWD/artifacts/browser-tools/node_modules/playwright"
python scripts/test_browser.py
```

已有工具时，可用 `SURVEY_NODE`、`SURVEY_PLAYWRIGHT_MODULE`、`SURVEY_CHROMIUM` 指定路径。脚本在隔离数据库中启动测试服务并执行四套网页流程。

第一阶段样本的新建与恢复：

```bash
python scripts/v1_baseline.py --output artifacts/v1-check
python scripts/v1_baseline.py --restore 提交文档/附件/v1-baseline.json --output artifacts/v1-restore
```

每个输出目录须尚不存在。脚本核对 4 个合成账号、3 份问卷和 8 份答卷的题意、字段、路径和统计，并检查应用重启后读取及未知 schema 版本拒绝。恢复只写入新建的隔离数据库，归档不含明文密码和会话 cookie。

## 提交材料

本仓库提供前后端、MongoDB schema、配置、测试和课程要求的说明文档，文件对应关系见[提交清单](提交文档/提交清单.md)。水杉平台使用 `2026_ECNU_PJ1_1_任北鸣.pdf`；仓库保存同名报告供查阅，平台上传是单独的提交步骤。
