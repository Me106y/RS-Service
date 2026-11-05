# RS-Service API / MCP

只绑 `127.0.0.1`。浏览器经 `SIRIAgent` 反代访问 REST；DSH Agent 通过 MCP 调用。

## REST `/api/v1`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 卫星数、最近同步、同步状态 |
| GET | `/satellites?category=&orbit=&payload_public=&limit=&offset=` | 卫星列表，默认 50 条 |
| GET | `/aoi` | AOI 列表（浙江省级 / 市级 / 区县） |
| GET | `/aoi/hangzhou` | 杭州 WGS84 市界 GeoJSON（Amap 转换，6406 点） |
| GET | `/positions?at=ISO` | 65 颗星下点；`at` 省略为当前，支持时间轴 |
| GET | `/satellites/{norad}` | 卫星 + 传感器 + 当前 TLE |
| GET | `/satellites/{norad}/passes?hours=24&lat=30.27&lon=120.16&min_elev=5&step=30` | 过境窗口；5° 可见，10°+太阳>10° 标 `optical_imaging_ok` |
| GET | `/satellites/{norad}/track?hours=2&step=60&at=ISO` | 星下点轨迹；`at` 支持时间轴 |
| GET | `/satellites/{norad}/position` | 当前星下点 |
| GET | `/sync/status` | 最近同步尝试、重试状态、TLE 最大年龄 |

所有响应带：

```json
{
  "accuracy_note": "TLE/SGP4; along-track ~1 km/day; not for sub-meter planning"
}
```

当前 65 颗均为 `payload_public=true`；接口仍保留 `payload_public=false` 的脱敏分支，只返回 `sensor_type/confidence`，不返回 `raw_spec`、分辨率、幅宽。

示例：

```bash
curl -s http://127.0.0.1:8766/api/v1/health
curl -s 'http://127.0.0.1:8766/api/v1/satellites?category=commercial&limit=5'
curl -s http://127.0.0.1:8766/api/v1/satellites/40118
```

## 成图规划 `/api/v1/plans`

5 个选星任务由 RS-Service 的后端流程承担；前端只提交参数、轮询状态、请求报告。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/plan-options` | 任务、必填/高级字段、默认值、传感器类型、AOI 列表、可规划数量 |
| POST | `/plans` | 建任务，返回 202 + `job_id`；任务在进程内排队 |
| GET | `/plans/{job_id}` | `queued` / `running` / `completed` / `failed` + 结果或错误 |
| POST | `/plans/{job_id}/report` | 用已完成结果渲染四节 HTML，返回 `reports/imaging-plan/...` 相对路径 |
| GET | `/plans/{job_id}/report` | 报告 HTML 本体 |

任务：`task1` 最少星达覆盖率、`task2` 最少星达重访、`task3` 给定星数最大化达标面积、
`task4` 给定星数最小化面积分位 MRT、`task5` 给定星数最小化 ART。

- AOI 支持省/市/区县 adcode 或 `POLYGON` WKT；只允许浙江省内。
- 目录 65 颗（61 光学 + 4 高光谱）；SAR 缺原生入射角带，暂不参与。
- 云量默认按 Open-Meteo 逐小时 `cloud_cover > 70%` 剔除访问；取不到时不过滤并在 assumptions 说明。
- `auto_expand` 只允许 `task1`：先按请求窗口算，不达标再扩到 72h（最多 2 次求解）；7 天请手动把 `horizon_h` 设成 168。结果 `window` 返回请求/实际窗口和 `expanded`，报告按实际窗口展示。
- TLE 年龄按 `tle_record.epoch_utc` 实时计算：>72h 标 degraded，全部候选 >168h 时返回 `tle_stale`；`plan-options.tle` 暴露 `max_age_hours` / `degraded` / `stale`。
- `rs-service-sync.timer` 每天 03:00 同步；TLE 过期时先跑一次 `rs-service sync`。
- 任务状态在进程内；重启丢任务，`reports/imaging-plan/<job_id>/` 下的报告文件保留，`latest` 指向最近一次。

## MCP（Streamable HTTP）

启动：`rs-service serve-mcp --port 8765`，端点为 `http://127.0.0.1:8765/mcp`。
DSH 侧工具名带前缀：`mcp__rs-service__<tool>`。

| 工具 | 入参 | 出参 |
| --- | --- | --- |
| `list_satellites` | `category?`, `orbit?` | 65 颗摘要（norad/name/category/orbit/payload_public） |
| `get_satellite` | `sat_id` | 目录 + 传感器摘要 + TLE 年龄 |
| `next_passes` | `sat_id`, `hours?`, `lat?`, `lon?`, `min_elev?` | 过境窗口（AOS/LOS/TCA/最大仰角/太阳高度/光学可用） |
| `ground_track` | `sat_id`, `hours?`, `step_s?` | 星下点轨迹 |
| `list_positions` | — | 65 颗当前星下点（批量） |
| `current_position` | `sat_id` | 当前星下点 |
| `sync_status` | — | 最近同步、重试状态、TLE 最大年龄 |
| `list_aoi` | `level?`, `query?` | 浙江省级 / 市级 / 区县 AOI（adcode、名称、bbox、面积） |

后续工具（`get_tle`、`propagate`、`run_command`）按需求文档 §5.2 逐步补齐；每个工具同时更新本文件与 `COMMANDS.md`。

## 错误约定

- 卫星不存在：`{"ok": false, "error": "not_found", "sat_id": ...}`。
- 同步失败：`sync_status.last_run.status` 为 `retrying` / `abandoned`，并带 `error_code`。
- TLE 过期：`age_hours > 72` 时由上层标记 `degraded`；`> 168` 拒绝用于过境计算。
