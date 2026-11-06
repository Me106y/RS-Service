# AGENTS.md

> 给 Agent 的项目指令。只放两类内容：**项目全貌** + **违反就会出错的硬规则**；细节一律链到文档。
> 真理源：需求、表结构、同步与精度口径看 `../SIRIAgent/docs/PHASE2-REQUIREMENTS.md`（§4、§5）；接口契约看 `docs/api.md`。
> 本文与 docs 冲突时以 docs 为准，并在同一个 PR 里修正本文。

## 项目概述

`RS-Service` 是 SIRIAgent 的星历后端：CelesTrak TLE 日同步 → PostgreSQL → SGP4 过境/覆盖/规划 → REST + MCP + CLI。

- **形态**：FastAPI `127.0.0.1:8766/api/v1`；MCP Streamable HTTP `127.0.0.1:8765/mcp`（DSH 侧工具名 `mcp__rs-service__<tool>`）；CLI `rs-service`。三个入口共用 service 层。
- **技术栈**：Python 3.12 + uv；FastAPI · SQLAlchemy 2 · Alembic · psycopg3 · sgp4/skyfield/numpy · typer · mcp。
- **数据**：运行数据在 PostgreSQL `rs_service`（DSN 来自 `.env` 的 `RS_DATABASE_URL`）；仓库只放迁移、`data/` 种子与测试夹具；单测用临时 SQLite。
- **部署**：用户级 systemd 单元 `rs-service-api`、`rs-service-mcp`、`rs-service-sync.timer`；同步每天 03:00 Asia/Shanghai。

## 仓库里真实存在什么

```sh
ls src/rs_service src/rs_service/astro src/rs_service/api src/rs_service/mcp
ls migrations/versions tests data
```

- 只有 `data/` 下的种子与 AOI GeoJSON 进仓库；数据库文件、`.venv/`、缓存不进。
- `migrations/versions/` 只追加：已合并的迁移不改，改表新开一个 revision。
- 卫星数量与公开标记只认 `data/satellites_seed.json`；代码、README、本文件都不另抄数字。

## 快速命令

| 场景 | 命令 |
|---|---|
| 一次性环境 | `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"` |
| 测试 | `uv run pytest` |
| Lint | `uv run ruff check .` |
| 迁移 | `uv run alembic upgrade head` |
| 建库 / 同步 | `.venv/bin/rs-service init`、`.venv/bin/rs-service sync`（先 `--dry-run`） |
| 起服务 | `.venv/bin/rs-service serve-api --port 8766`、`.venv/bin/rs-service serve-mcp --port 8765` |
| 重启线上单元 | `systemctl --user restart rs-service-api rs-service-mcp` |
| 状态 | `.venv/bin/rs-service status` |

- `uv` 在 `~/.local/bin/uv`，不在 systemd 单元的 PATH 里：服务、脚本、文档示例用 `.venv/bin/...`，不依赖 PATH。
- MCP 进程不热加载：改了 `mcp/`、`astro/`、`db/` 后必须重启 `rs-service-api` 与 `rs-service-mcp` 两个单元。
- `.env` 只在本机存在；仓库只提交 `.env.example`。

## 架构：四条不能破的线

**① 入口薄，逻辑在 service。** `api/`、`mcp/`、`cli.py` 只做参数校验与协议转换；业务在 `astro/`、`sync/`、`db/`。新增能力先落 service，再包三个入口。

**② 只绑回环。** REST 与 MCP 只监听 `127.0.0.1`；浏览器经 SIRIAgent 反代，永不直连 `8766` / `8765`。

**③ 口径随结果走。** 每个对外结果带 `accuracy_note`；TLE >72h 标 `degraded`，>7d 拒绝过境计算；`payload_public=false` 的卫星只返回轨道/可见性，不返回分辨率、幅宽、`raw_spec`。

**④ 数据与迁移同进同出。** 改表 = 改模型 + 新 Alembic revision + 空库 `alembic upgrade head` 通过；不手改数据库，不把数据库文件或导出数据提交进仓库。

## 硬规则（违反即为缺陷）

### 同步（CelesTrak）

- 每天 1 次、最多 3 次重试（`300,900,2700` 秒）、失败当天 `abandoned`，次日自动恢复。
- 非 200 终止本次尝试；403 “not updated” 不重试；同日不重复下载。
- 状态写 `sync_state`；重启后不重复抓取；失败保留上一版数据，不写半套 TLE。

### 安全

- `.env`、DSN 密码、代理凭据不进仓库；仓库只留 `.env.example`。
- 日志不打印 DSN 密码或完整连接串。
- 命令只走注册子命令，不执行任意 shell 字符串（`run_command` 落地后同样适用）。

### 代码

- 新模块 ≤600 行；类型注解齐全；错误路径结构化日志，不吞异常。
- `ruff` 通过（`E,F,I,UP,B`，行宽 100）。

### 语言

- 文档、README、PR 正文、提交正文用中文；提交标题用英文（见下）。
- 代码标识符、命令、日志、协议字段、错误码保持原文，不翻译。

## 提交与 PR：`main` 只接受 PR

没有直推。代码、文档、一行修正、配置、迁移都一样：**开分支 → 提交 → push → `gh pr create` → 门禁 + 评审 → 合并**。所有改动经 PR 落到 `main`。

- **起分支**：先 `git fetch origin`，从 `origin/main` 开 `<type>/<kebab-slug>`（如 `docs/agents-md`）；agent 建的分支用 `agent/<slug>-<hash>`。
- **开 PR**：`gh pr create --base main --fill`，再按模板补正文。
- **一个 PR 只做一件事**：改了接口（REST 字段、MCP 工具名/入参、错误码）就同时开 `SIRIAgent` 的 PR 更新 `skills/*/COMMANDS.md` 与前端，两边互相链接；先合本仓，再合前端。
- **提交信息**：Conventional Commits；**标题英文**、祈使句、小写开头、不加句号；**正文中文**说清为什么。类型：`feat` `fix` `docs` `test` `refactor` `perf` `ci` `build` `chore`。
  - 例：`fix(settings): resolve .env from the repository root`
  - 一个提交只做一件事；重构与行为变更不混。
- **PR 标题**沿用同一格式；**正文按 `.github/pull_request_template.md` 填**：做了什么 / 为什么 / 改了哪一层 / 硬规则自查 / 怎么验证 / 风险与回滚。
- **agent 提的 PR，正文第一行写 `This is by an automated agent, not a human.`**
- **门禁（命令与结果贴进 PR 正文）**：
  - `uv run pytest` 必须绿；
  - `uv run ruff check .` 必须干净；
  - 改表：在空库上 `uv run alembic upgrade head` 通过。
  - A7 的 PR CI 落地后，以 CI 绿为准，本地命令仍要跑。
- **合并由人做**：agent 可以开 PR、推修复，不自己点合并；评审意见（含 CodeRabbit）逐条处理，或写明不采纳的理由。
- **推送双写**：`git push` 要同时推到 Gitee 镜像，别只推 GitHub：
  ```bash
  git push origin <branch>
  git push gitee  <branch>
  ```
  远端已配好（`git remote -v` 里应有 `gitee`）；新克隆先
  `git remote add gitee https://gitee.com/su4iky/<repo>.git`。
  `main` 合并后由 `.github/workflows/mirror-to-gitee.yml` 自动镜像；
  分支也双推，是为了不依赖 Actions（可能排队或失败）就能让镜像跟上。
- **合并方式**：Squash merge，PR 标题即最终提交信息；合并后删分支。评审期间用追加提交修意见，不 force-push（会打断评审上下文）。
- **部署随合并自动发生**：`main` 有新提交后约 2 分钟内，本机 autoship 自动
  `uv pip install -e .` 并重启 `rs-service-api` / `rs-service-mcp`；
  结果看 `autoship status`、`autoship logs RS-Service`，失败会弹通知。
  PR 里写清这次改动是否影响运行中的服务、回滚要做什么。
- **改需求/表结构/口径**：先改 `../SIRIAgent/docs/PHASE2-REQUIREMENTS.md`（§12.3 变更记录），再改代码与迁移；不在代码里悄悄偏离文档。

## 完成定义：跑通才算完

改代码不算完成，验证过才算：

1. 门禁绿：`uv run pytest`、`uv run ruff check .`。
2. 改了表：空库 `alembic upgrade head` 通过；迁移与模型一致。
3. 同步相关：成功、重试、`abandoned` 三条路径都有测试；失败保留上一版数据。
4. 接口相关：REST/MCP 契约测试更新；`docs/api.md` 与 SIRIAgent 的 `COMMANDS.md` 同步。
5. `git status` 干净：没有 `.env`、数据库文件、DSN 密码、缓存混进提交。

## 文档导航

| 想了解 | 去哪看 |
|---|---|
| 需求、表结构、同步、精度 | `../SIRIAgent/docs/PHASE2-REQUIREMENTS.md` §4、§5 |
| REST / MCP 契约与错误约定 | `docs/api.md` |
| 部署与 systemd | `deploy/README.md`、`README.md` |
| 报错与真正原因 | `../SIRIAgent/docs/TROUBLESHOOTING.md` |
| 评审与 PR 要填什么 | `.github/pull_request_template.md` |

<!-- 全文控制在 200 行内：放不下的写进 docs/，这里只加一行链接。 -->
