# RS-Service

SIRIAgent 的星历与成图计算后端：国产遥感卫星目录、每日 TLE 同步、过境与覆盖计算、成图规划，以及 REST / MCP / CLI 三种取数方式。

**日常使用请直接打开 <https://siriagent.dpdns.org>** —— 这个服务只绑 `127.0.0.1`，由 SIRIAgent 反代并展示。本仓库适合需要直接调数据、接 Agent 或做二次开发的场景。

## 提供什么

### 卫星与星历

- 65 颗国产遥感卫星（61 颗光学 + 4 颗高光谱）的目录、传感器参数、TLE 历史与过境窗口；SAR 因缺原生入射角带不参与成图。
- TLE 每天 03:00（Asia/Shanghai）从 CelesTrak 同步一次；失败最多重试 3 次，当天不再抓取。TLE 年龄 >72 小时标记精度下降，>7 天拒绝用于过境计算。
- 实时星下点、批量位置、2 小时星下点轨迹。
- 过境窗口：AOS / TCA / LOS、最高仰角、方位角、时长、太阳高度，并按「5° 可见、10° 成像、光学另需太阳高度 >10°」给出 `optical_imaging_ok`；覆盖选星同样执行 10° 最小仰角和光学/高光谱太阳高度 ≥10° 门限，SAR 不看太阳。
- AOI 目录：浙江省内省级 / 市级 / 区县，以及杭州 WGS84 市界 GeoJSON。

### 成图规划

- 5 个选星任务：最少星达到覆盖率（面积口径）、最少星达到重访、给定星数最大化覆盖面积（面积口径）、给定星数最小化面积分位 MRT、给定星数最小化 ART。
- 范围支持浙江省内省 / 市 / 区县 adcode，或 `POLYGON` WKT；时间窗 1–168 小时；task1 支持不达标自动扩到 72 小时。
- 云量默认用 Open-Meteo 逐小时 `cloud_cover > 70%` 剔除访问；取不到数据时不作过滤，并在结果里说明。
- 求解结果可渲染为四节 HTML 报告：卫星构成 / 覆盖与回放 / 推荐计划 / 口径与精度。报告逐星给出过境、平均覆盖与最大覆盖，并交代统计口径。

### 三种取数方式

- **REST** `/api/v1`：给前端和脚本用，星历类响应都带 `accuracy_note`。
- **MCP**：给 DSH Agent 用，工具名带 `mcp__rs-service__` 前缀。
- **CLI**：`rs-service init / sync / status / serve-api / serve-mcp`，REST 与 MCP 是同一实现的薄包装。

## 怎么用

### 通过 SIRIAgent（推荐）

打开 <https://siriagent.dpdns.org> 登录后：左栏看卫星与过境，右栏问 Agent，点「卫星成图」提交规划并生成报告。无需直接访问本服务。

### 通过 REST

服务在本机 `8766` 启动后：

```bash
# 健康与同步状态
curl -s http://127.0.0.1:8766/api/v1/health
curl -s http://127.0.0.1:8766/api/v1/sync/status

# 卫星目录与单星详情
curl -s 'http://127.0.0.1:8766/api/v1/satellites?category=commercial&limit=5'
curl -s http://127.0.0.1:8766/api/v1/satellites/40118

# 当前星下点与未来 24h 过境（杭州）
curl -s http://127.0.0.1:8766/api/v1/positions
curl -s 'http://127.0.0.1:8766/api/v1/satellites/40118/passes?hours=24&lat=30.27&lon=120.16'
```

完整端点、参数与错误约定见 [`docs/api.md`](docs/api.md)；浏览器里可打开 `http://127.0.0.1:8766/docs` 看 Swagger。

### 通过 MCP

`rs-service serve-mcp --port 8765`，端点 `http://127.0.0.1:8765/mcp`。可用工具：

| 工具 | 用途 |
| --- | --- |
| `list_satellites` | 按类别 / 轨道筛选卫星 |
| `get_satellite` | 单星目录、传感器摘要、TLE 年龄 |
| `next_passes` | 指定 AOI 的未来过境窗口 |
| `ground_track` | 星下点轨迹 |
| `list_positions` / `current_position` | 批量 / 单星星下点 |
| `sync_status` | 最近同步、重试状态、TLE 最大年龄 |
| `list_aoi` | 浙江省级 / 市级 / 区县 AOI |

### 通过 CLI

```bash
.venv/bin/rs-service status        # 卫星数、TLE 年龄、最近同步
.venv/bin/rs-service sync          # 手动同步（默认 3 次重试）
.venv/bin/rs-service serve-api --port 8766
.venv/bin/rs-service serve-mcp --port 8765
```

## 精度与口径

| 项 | 口径 |
| --- | --- |
| 轨道 | TLE + SGP4，LEO 沿轨误差约 1 km/天；过境时间约 ±1 分钟 |
| 可见 / 成像 | 可见 5°；成像最高仰角 ≥10°；光学另需太阳高度 >10°，SAR 不看太阳 |
| 足迹 | 边缘误差按 ±2–5 km 对待，不展示为精确边界 |
| 覆盖率 / 重访 | 估算值（`estimate`），不代表保证重访 |
| TLE 年龄 | ≤72 h 正常（24–72 h 可能带 `stale` 提示）；72 h–7 d 标 `degraded`；>7 d 拒绝计算 |
| 坐标与时间 | 对外统一 WGS84 与 UTC ISO8601 |

星历类结果都带 `accuracy_note`；结论用于可视化与可行性初判，不能用于亚米级任务规划或碰撞规避。

## 本地启动

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/rs-service init       # 建表 + 种子卫星
.venv/bin/rs-service status
.venv/bin/rs-service serve-api --port 8766
```

数据库为 PostgreSQL，DSN 由 `.env` 的 `RS_DATABASE_URL` 提供（默认 `127.0.0.1:5432/rs_service`）。

## 限制

- 只绑 `127.0.0.1`，不暴露公网；公网访问统一经 SIRIAgent。
- 成图范围仅限浙江省内；目录不含 SAR 星。
- 成图任务状态在进程内，重启会丢失排队任务；报告按任务落在 `reports/imaging-plan/<task>/<run_id>/`，`latest` 指向最近一次。
- 任务并发受进程内单队列限制。

## 文档

- 接口与字段：[`docs/api.md`](docs/api.md)
- 仓库工作约定：[`AGENTS.md`](AGENTS.md)
- 需求与验收（唯一事实源）：[`SIRIAgent/docs/PHASE2-REQUIREMENTS.md`](https://github.com/Me106y/SIRIAgent/blob/main/docs/PHASE2-REQUIREMENTS.md)
- 前端与 Agent 平台：[`SIRIAgent`](https://github.com/Me106y/SIRIAgent)
