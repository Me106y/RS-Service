# RS-Service API / MCP

只绑 `127.0.0.1`。浏览器经 `SIRIAgent` 反代访问 REST；DSH Agent 通过 MCP 调用。

## REST `/api/v1`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 卫星数、最近同步、同步状态 |
| GET | `/satellites?category=&orbit=&payload_public=&limit=&offset=` | 卫星列表，默认 50 条 |
| GET | `/aoi` | AOI 列表（杭州 330100，16819 km²） |
| GET | `/aoi/hangzhou` | 杭州 WGS84 市界 GeoJSON（Amap 转换，6406 点） |
| GET | `/positions?at=ISO` | 111 颗星下点；`at` 省略为当前，支持时间轴 |
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

`payload_public=false` 的 13 颗卫星只返回 `sensor_type/confidence`，不返回 `raw_spec`、分辨率、幅宽。

示例：

```bash
curl -s http://127.0.0.1:8766/api/v1/health
curl -s 'http://127.0.0.1:8766/api/v1/satellites?category=commercial&limit=5'
curl -s http://127.0.0.1:8766/api/v1/satellites/40118
```

## MCP（Streamable HTTP）

启动：`rs-service serve-mcp --port 8765`，端点为 `http://127.0.0.1:8765/mcp`。
DSH 侧工具名带前缀：`mcp__rs-service__<tool>`。

| 工具 | 入参 | 出参 |
| --- | --- | --- |
| `list_satellites` | `category?`, `orbit?` | 111 颗摘要（norad/name/category/orbit/payload_public） |
| `get_satellite` | `sat_id` | 目录 + 传感器摘要 + TLE 年龄 |
| `next_passes` | `sat_id`, `hours?`, `lat?`, `lon?`, `min_elev?` | 过境窗口（AOS/LOS/TCA/最大仰角/太阳高度/光学可用） |
| `ground_track` | `sat_id`, `hours?`, `step_s?` | 星下点轨迹 |
| `list_positions` | — | 111 颗当前星下点（批量） |
| `current_position` | `sat_id` | 当前星下点 |
| `sync_status` | — | 最近同步、重试状态、TLE 最大年龄 |

后续工具（`get_tle`、`propagate`、`run_command`）按需求文档 §5.2 逐步补齐；每个工具同时更新本文件与 `COMMANDS.md`。

## 错误约定

- 卫星不存在：`{"ok": false, "error": "not_found", "sat_id": ...}`。
- 同步失败：`sync_status.last_run.status` 为 `retrying` / `abandoned`，并带 `error_code`。
- TLE 过期：`age_hours > 72` 时由上层标记 `degraded`；`> 168` 拒绝用于过境计算。
