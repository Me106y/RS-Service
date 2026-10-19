# AGENTS.md — RS-Service

本仓是 SIRIAgent 的星历后端。需求、表结构、精度口径以
`../SIRIAgent/docs/PHASE2-REQUIREMENTS.md` 为唯一事实源；本文件只写后端工作约定。

## 范围

- 负责：星历库、每日同步、REST、MCP、CLI、过境/覆盖计算。
- 不负责：前端、DSH 会话、门禁；这些在 `SIRIAgent`。
- 运行数据留在 `~/.local/share/rs-service/rs.db`；仓库只放迁移、种子目录与测试夹具。

## 数据与同步

- 12 张表见需求文档 §4.2；改表必须同时改 Alembic 迁移与本文档引用。
- 同步每天 1 次（03:00 Asia/Shanghai）、最多 3 次重试、失败当天 `abandoned`。
- 状态写 `sync_state`；重启后不重复抓取；失败保留上一版数据。
- 抓取遵守 CelesTrak 纪律：非 200 终止本次尝试、403 "not updated" 不重试、同日不重复下载。

## 精度口径

- SGP4 在 LEO 约 1 km/天；只用于可视化、可行性初判、过境时间 ±1 分钟。
- 5° 可视化 / 10° 成像 / 光学太阳高度 >10°；足迹 ±2–5 km。
- 所有对外结果带 `accuracy_note`；TLE >72h 标 degraded，>7d 拒绝过境计算。
- `payload_public=false` 的卫星只返回轨道/可见性，不返回分辨率。

## 接口

- REST 与 MCP 共用 `service` 层；MCP 工具不写业务逻辑。
- 只绑 `127.0.0.1`；浏览器经 SIRIAgent 反代。
- 新增 MCP 工具：更新 `docs/api.md`、`tests/` 与 SIRIAgent 的 `COMMANDS.md`。
- 命令执行走注册子命令（`run_command`），不执行任意 shell 字符串。

## 代码与验证

- Python 3.12 + uv；FastAPI + SQLAlchemy 2 + Alembic；SQLite WAL。
- 新模块 ≤600 行；类型注解；错误路径结构化日志。
- 提交前：`uv run pytest`；改表时 `uv run alembic upgrade head` 在空库上通过。
- 提交：`feat(scope): ...` / `fix(scope): ...` / `docs: ...`；一次提交只做一件事。

## 完成标准

1. 需求文档对应验收项可复现；
2. 迁移在空库上可升级；
3. 同步状态机测试覆盖成功、重试、abandoned；
4. `data/` 无数据库文件，`git status` 干净。
