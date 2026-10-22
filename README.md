# MongoDB 问卷系统

当代数据管理系统第一阶段个人作业，任北鸣。FastAPI + PyMongo + Jinja2 + 原生 JavaScript；所有持久业务数据存 MongoDB。支持注册登录、问卷草稿/发布/关闭、单选/多选/文本/数字、约束和可配置跳转、回退清理、匿名/重复限制、整卷/单题统计与内容分页。

[项目完成报告](提交文档/项目完成报告.md) · [系统与数据库设计](提交文档/系统与数据库设计.md) · [运行与 API](提交文档/运行与API说明.md) · [关键逻辑](提交文档/关键逻辑说明.md) · [测试结果](提交文档/测试用例与结果.md) · [AI 使用日志](提交文档/AI使用日志.md) · [提交清单](提交文档/提交清单.md)

仓库：[Rbmmmm/ecnu-dbms-2026-pj1](https://github.com/Rbmmmm/ecnu-dbms-2026-pj1)。报告文字与贡献归属仍待审核。

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

编辑 `.env` 中的 `SURVEY_DB` 为已确认的本人数据库；按课程约定优先只读测试阿里云，失败后使用本地。历史 W005 云端 ping 超时后采用本地，本轮未重测云端。此应用不会自动切换连接。不要把 `.env` 上传仓库。集合使用 `survey_` 前缀，保留实验 `users`。

```bash
python scripts/init_db.py
python scripts/run.py
```

打开 [本机问卷系统](http://127.0.0.1:8000)。启动创建缺失集合/索引并检查已有契约，不自动迁移不兼容数据。`/health` 检查连接、validator 与索引，当前显示 P4/W-009 是业务功能基线，不能代表 P6 或用户终审；未知存量 schema 版本会在启动时拒绝。

需要其他端口时同时将 `.env` 的 `SURVEY_ORIGIN` 改为 `http://127.0.0.1:18000`，然后执行：

```bash
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18000
```

## 使用

创建者注册、登录，创建问卷并添加题目、选项、校验与规则，保存后发布。另一浏览器会话注册填写者，从分享链接登录，按路径填写并确认提交；创建者打开统计。只有明确提交才保存答卷；匿名默认关闭，每账号默认一次。发布后题意冻结，仍可修改截止或关闭。查看/刷新统计读取已保存答卷。

单选同时显示按账号去重的人数与答卷次数；多选计选择次数；数字均值排除空答、保留 0。回退删除确定跳过的答案，尚未确定可达的后续答案只在当前页暂存。未提交输入不保证刷新恢复；匿名不自动清除填写者主动写入文本的姓名；统计不同查询不承诺同一快照。

## 运行固定测试

真实 MongoDB 必须在本机 127.0.0.1:27017。下列测试自行创建本轮隔离库并清理，不继承业务连接，不操作业务库。

```bash
python -m pytest tests/test_p2.py tests/test_w007.py tests/test_w008.py tests/test_p4.py -o addopts= -q
python scripts/v1_baseline.py --output artifacts/v1-check
python scripts/v1_baseline.py --restore 提交文档/附件/v1-baseline.json --output artifacts/v1-restore
```

每个 v1 输出路径须尚不存在。封存恢复会验证 4 个合成账号、3 份问卷和 8 份答卷的题意/字段/路径、全类型统计、应用重启和未知 schema 启动拒绝；只恢复到新建的本轮隔离库。归档不含明文密码和会话 cookie，恢复读取使用新生成的隔离测试会话。

完整 Git 仓库另提供 `scripts/verify.py` 的 P2/W007/P3/P4/P6 全量入口。精简运行复现包只用于验证安装、上述 101 个 API 案例及 v1 恢复，不替代 GitHub 完整源码提交或用户终审。完整网页回归还需要 Node.js、Playwright 和 Chromium；P6 还通过 Poppler 的 `pdfinfo`、`pdftotext` 检查报告。仅启动应用或运行 API 测试不需要这些工具。

如未安装浏览器工具，在项目根目录执行（Playwright 模块放在被 Git 忽略的 `artifacts/` 中，浏览器安装到默认用户缓存）：

```bash
npm install --prefix artifacts/browser-tools --no-save playwright@1.62.1
node artifacts/browser-tools/node_modules/playwright/cli.js install chromium
export SURVEY_NODE="$(command -v node)"
export SURVEY_PLAYWRIGHT_MODULE="$PWD/artifacts/browser-tools/node_modules/playwright"
export SURVEY_CHROMIUM="$(node -p 'require(process.env.SURVEY_PLAYWRIGHT_MODULE).chromium.executablePath()')"
```

Poppler 在 macOS 可用 `brew install poppler`，Ubuntu/Debian 可用 `sudo apt-get install poppler-utils`。只运行完整业务回归时执行 `python scripts/verify.py --stage P4`。P6 入口在完整 Git 仓库执行：

```bash
python scripts/verify.py --stage P6
```

也可用 `SURVEY_NODE`、`SURVEY_PLAYWRIGHT_MODULE`、`SURVEY_CHROMIUM` 配置已有浏览器工具。P6 实际重跑固定 P4 101 案例及四套网页，在不含实际 `.env` 的复现包中创建独立 Python 环境、安装锁定依赖、重跑全部 API 案例、创建并恢复 v1 样本。证据保存到 `artifacts/p6-运行号/`。缺文件、篡改包、过期审核、缺用户审核或 GitHub 发布核对未完成均不能成为 P6 通过；`--changed` 未实现。

## 提交状态

用户确认截止 2026 年 10 月 23 日 00:00（北京时间）。GitHub 需要可运行前后端、测试与本 README；水杉提交 `2026_ECNU_PJ1_1_任北鸣.pdf`，最前面放仓库链接。本仓库提供源码和测试；报告仍为待审核材料，尚未提交水杉。公开历史从第一阶段发布快照开始，保留现有核心结构、真实 AI 日志与阶段证据；含个人数据库标识的旧本地 Git 历史和旧内部 ZIP 不公开。完整 Git 仓库的当前证据及交接在 `开发文档/agent-handoff.md`；历史开发资料保留供追溯，没有整体复制为报告附件。
