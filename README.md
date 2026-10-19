# RS-Service

SIRIAgent 的星历数据服务：本地数据库、每日 TLE 同步、REST、MCP 与 CLI。

## 职责

- 管理 111 颗国产遥感卫星的目录、TLE 历史、传感器与过境窗口。
- 每天 03:00 从 CelesTrak GP 同步一次；失败最多重试 3 次，当天不再抓取。
- 对 `SIRIAgent` 提供 `127.0.0.1` 上的 REST 与 MCP；不暴露公网。
- CLI 是唯一实现，MCP 工具是薄包装。

## 快速开始

```bash
cd "/home/siri/桌面/RS-Service"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# 初始化数据库（建表 + 种子卫星）
.venv/bin/rs-service init

# 查看状态
.venv/bin/rs-service status

# 手动同步（默认 3 次重试；测试时可用 --retry-delay 0）
.venv/bin/rs-service sync --dry-run
.venv/bin/rs-service sync

# 启动 REST 与 MCP
.venv/bin/rs-service serve-api --port 8766
.venv/bin/rs-service serve-mcp --port 8765
```

数据库默认在 `~/.local/share/rs-service/rs.db`，可用 `RS_DB_PATH` 覆盖。

## 目录

```text
src/rs_service/
├── api/        FastAPI /api/v1
├── mcp/        MCP server（Streamable HTTP）
├── db/         SQLAlchemy 模型、仓储
├── sync/       CelesTrak 抓取、解析、重试状态机
├── astro/      SGP4、过境、覆盖
├── cli.py      rs-service init/sync/status/serve
└── settings.py
migrations/     Alembic
data/           satellites_seed.json（111 颗目录，不含运行数据）
tests/
```

## 文档

- 需求与验收：`../SIRIAgent/docs/PHASE2-REQUIREMENTS.md`（唯一事实源）。
- 本仓工作约定：`AGENTS.md`。
