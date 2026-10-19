# RS-Service API / MCP

只绑 `127.0.0.1`。浏览器经 `SIRIAgent` 反代访问 REST；DSH Agent 通过 MCP 调用。

## REST `/api/v1`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 卫星数、最近同步、同步状态 |
| GET | `/satellites?category=&orbit=&payload_public=&limit=&offset=` | 卫星列表，默认 50 条 |
| GET | `/satellites/{norad}` | 卫星 + 传感器 + 当前 TLE |
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
| `sync_status` | — | 最近同步、重试状态、TLE 最大年龄 |

后续工具（`get_tle`、`propagate`、`ground_track`、`next_passes`、`coverage_window`、`run_command`）按需求文档 §5.2 逐步补齐；每个工具同时更新本文件与 `COMMANDS.md`。

## 错误约定

- 卫星不存在：`{"ok": false, "error": "not_found", "sat_id": ...}`。
- 同步失败：`sync_status.last_run.status` 为 `retrying` / `abandoned`，并带 `error_code`。
- TLE 过期：`age_hours > 72` 时由上层标记 `degraded`；`> 168` 拒绝用于过境计算。
