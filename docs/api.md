# RS-Service API / MCP

只绑 `127.0.0.1`。浏览器经 `SIRIAgent` 反代访问 REST；DSH Agent 通过 MCP 调用。

## REST `/api/v1`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 卫星数、最近同步、同步状态 |
| GET | `/satellites?category=&orbit=&payload_public=&limit=&offset=` | 卫星列表，默认 50 条 |
| GET | `/aoi` | AOI 列表（浙江省级 / 市级 / 区县） |
| GET | `/aoi/hangzhou` | 杭州 WGS84 市界 GeoJSON（Amap 转换，6406 点） |
| GET | `/positions?at=ISO` | 215 颗星下点；`at` 省略为当前，支持时间轴 |
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

当前 215 颗均为 `payload_public=true`；接口仍保留 `payload_public=false` 的脱敏分支，只返回 `sensor_type/confidence`，不返回 `raw_spec`、分辨率、幅宽。

示例：

```bash
curl -s http://127.0.0.1:8766/api/v1/health
curl -s 'http://127.0.0.1:8766/api/v1/satellites?category=commercial&limit=5'
curl -s http://127.0.0.1:8766/api/v1/satellites/40118
```

## 成图规划 `/api/v1/plans`

v2.0.0 只有一个场景 `revisit`：给定区域 + 重访时间 T，求最少卫星；面积目标固定 100%。旧 task1~task5 的新入口已移除，旧报告仍可读。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/plan-options` | 单任务 `revisit`、T 档位、窗口规则、AOI（市/区县）、可规划数量、TLE 状态 |
| POST | `/plans` | `{operation:"revisit", aoi_code|aoi_wkt, revisit_hours, start_utc?, max_resolution_m?, cloud_filter?}`；返回 202 + `job_id` |
| GET | `/plans/{job_id}` | `queued` / `running` / `completed` / `failed` + `progress` + 结果或错误 |
| POST | `/plans/{job_id}/report` | 用已完成结果渲染四节 HTML，落 `reports/imaging-plan/revisit/<job_id>/` |
| GET | `/plans/{job_id}/report` | 报告 HTML 本体（按 `run_id` 扫任务子目录） |
| GET | `/plans/history` | 历史报告扁平列表（只读 `meta.json`）；新 run `task="revisit"`，旧 run 保留 `task1..task5` |

- 口径：`W = min(3T, 168h)`；T 只接受 24 / 48 / 72；面积目标固定 100，`allow_area_fallback=false`；`auto_expand=false`。T=72h 时 W 封顶 168h（2.33T），结果 `window.capped_by_tle=true` 并标注。
- 判定：每个 5 km 网格代表点的最长重访间隔（**含夜间与窗口首尾空档**）≤ T；全部网格达标才算可行。`summary_cards.qualified_area_pct` 是达标面积；`summary_cards.ceiling_area_pct` 是全部候选星一起算的“全目录上限”，不可行时用来区分“目录不够”与“算法没找到”。
- 结果字段：`targets.revisit_hours`、`targets.area_pct=100`、`targets.temporal_coverage_pct=null`；`window.window_rule="3T"`、`window.used_horizon_h`、`window.capped_by_tle`；`optimality ∈ proven_optimal|time_limited|infeasible_heuristic`；`suggestion` 仅在不可行且 ≤10m 时给出“放宽到 ≤1km 可 100%”的结构化建议（不自动切换）。
- 进度：`GET /plans/{job_id}` 的 `progress={stage,label,current,total,pct,elapsed_s,updated_at}`；阶段 `prepare/grid/opportunities/cloud/selection/metrics/playback/done`。机会阶段 `current/total` 是已传播卫星数；选星阶段 1/2、2/2。前端 1.2s 轮询，不提供 ETA。
- AOI：只支持市/区县 adcode 或浙江省内 `POLYGON` WKT；自定义面积 >20,000 km² 返回 `aoi_too_large`，省级 AOI 也返回 `aoi_too_large`。
- 载荷：只对光学开放；`sensor_types` 固定 `["optical"]`；`max_resolution_m` 只接受 1/2/5/10。目录 215 颗（211 光学 + 4 高光谱），≤10m 光学可规划 203 颗（含 3 颗 Sentinel-2、110 颗 PlanetScope Flock）；新增星按 `official/vendor/approx` 标 `confidence` 并保存 `source_url`，报告与目录可追溯。高光谱保留在星历，不参与本场景。
- 云量：默认**不过滤**；`cloud_filter=true` 时按 AOI 内网格代表点取 Open-Meteo 逐小时 `cloud_cover`，逐网格剔除 >70% 的访问；取不到时不过滤并在 assumptions 说明。
- 成像门限：非 SAR 最小仰角 ≥10°；光学另要求目标地面太阳高度 ≥10°（`RS_OPTICAL_SUN_MIN_DEG`，默认 10）；夜间/低仰角窗口不进入覆盖与选星。
- 选星限时：贪心 ≤60s（`greedy_time_limit_s`），ILP ≤30s（`selection_time_limit_s`）；到点返回当前最好集合，`optimality=time_limited`。不可行时不允许返回“0 颗星、0%”，必须有最佳努力与全目录上限。
- TLE 年龄按 `tle_record.epoch_utc` 实时计算：>72h 标 degraded，全部候选 >168h 时返回 `tle_stale`；`plan-options.tle` 暴露 `max_age_hours` / `degraded` / `stale`。
- 任务状态在进程内；重启丢任务，报告文件保留；前端用 localStorage 的 job_id 恢复，404 时提示“任务已失效”。
- 报告落盘：`reports/imaging-plan/revisit/<run_id>/{plan.json,report.html,meta.json}`；旧 task1~task5 目录保留；根上 `latest` 软链指向最近一份。
- 报告首屏 KPI：最少卫星数 / 达标面积（最长重访 ≤ T · 目标 100%）/ 全目录上限 / 评估窗口；不再展示时间覆盖率与面积加权 MRT 作为主 KPI；不可行时标题写“未达标，全目录上限 X%”，并给出放宽分辨率的实测建议。
- `GET /plans/history` 返回 `{ok, root, runs[], total, by_task}`；`runs` 按 `created_at` 倒序，字段沿用 `run_id/task/task_label/aoi_kind/area_name/window_start/window_end/sat_count/kpi/feasible/created_at/relative_report/relative_plan/has_report`；`revisit` 的 KPI 是「达标面积（≤T）」。

## 遥感解译 `/api/v1/interpret`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/interpret/images` | 购买 / 上传 / 示例图清单（kind/group/name/path/宽高） |
| POST | `/interpret/change-detection` | 变化检测（FlickCD，双图）；`{before_path, after_path}` |
| POST | `/interpret/object-detection` | 目标检测（YOLOX-S COCO 80 类，单图）；`{image_path}` |
| POST | `/interpret/semantic-segmentation` | 语义分割（SparseMask OpenEarthMap 8 类，单图）；`{image_path}` |
| POST | `/interpret/process-image` | 图像处理（灰度 / Canny / 降噪）；`{image_path, operator, level?}` |

- 路径只允许 `workspace/` 或 `demo/` 下；越界 403、文件不存在 422、变化检测超时 504。
- 结果写 `SIRIAgent/reports/interpret/<run_id>/`（`mask.png` / `object_annotated.png` / `semantic_seg_map.png` + `semantic_seg_overlay.png` / `result.json`）。
- 每个响应带 `accuracy_note`；目标检测 `accuracy_note` 为「YOLOX-S 估计（阈值 0.25 / NMS 0.45），结果需人工复核；输入限制最大边 2048」。
- 目标检测权重 `data/models/object_detection/yolox_s.pth`（Megvii 官方 COCO 预训练，Apache-2.0，约 68.7MB）不入库，用 `.venv/bin/python scripts/fetch_models.py yolox` 拉取（ghproxy 镜像 + SHA256 校验）。
- 语义分割权重 `data/models/semantic_segmentation/sparsemask_openearthmap.onnx`（MIT 代码 / OpenEarthMap 权重 CC BY-NC-SA，限所内非商用，11.9MB）不入库，用 `scripts/fetch_models.py sparsemask` 拉取；类别表随权重目录 `sparsemask_config.json`（8 类 + 调色板，入库）。
- 图像处理档位：`canny` ∈ soft/normal/sharp（默认 normal）、`denoise` ∈ light/medium/strong（默认 light）；灰度忽略档位。
- 语义分割 `accuracy_note`：「SparseMask（OpenEarthMap 8 类预训练）估计，逐像素类别需人工复核；输入等比缩到长边 1024 后推理，再最近邻上采样回原尺寸」。

## MCP（Streamable HTTP）

启动：`rs-service serve-mcp --port 8765`，端点为 `http://127.0.0.1:8765/mcp`。
DSH 侧工具名带前缀：`mcp__rs-service__<tool>`。

| 工具 | 入参 | 出参 |
| --- | --- | --- |
| `list_satellites` | `category?`, `orbit?` | 215 颗摘要（norad/name/category/orbit/payload_public） |
| `get_satellite` | `sat_id` | 目录 + 传感器摘要 + TLE 年龄 |
| `next_passes` | `sat_id`, `hours?`, `lat?`, `lon?`, `min_elev?` | 过境窗口（AOS/LOS/TCA/最大仰角/太阳高度/光学可用） |
| `ground_track` | `sat_id`, `hours?`, `step_s?` | 星下点轨迹 |
| `list_positions` | — | 215 颗当前星下点（批量） |
| `current_position` | `sat_id` | 当前星下点 |
| `sync_status` | — | 最近同步、重试状态、TLE 最大年龄 |
| `list_aoi` | `level?`, `query?` | 浙江省级 / 市级 / 区县 AOI（adcode、名称、bbox、面积） |
| `list_workspace_images` | — | 购买 / 上传 / 示例图清单 |
| `detect_change` | `before_path`, `after_path` | 掩膜路径、变化像素/占比、连通域、耗时、`accuracy_note` |
| `detect_objects` | `image_path` | 标注图路径、目标数/类别数、检测框列表、耗时、`accuracy_note` |
| `segment_semantics` | `image_path` | 分类专题图/叠加图路径、各类面积占比、类别数、耗时、`accuracy_note` |
| `process_image` | `image_path`, `operator`(`grayscale`\|`canny`\|`denoise`), `level?` | 结果图路径、算子/档位、尺寸、耗时、`accuracy_note` |

后续工具（`get_tle`、`propagate`、`run_command`）按需求文档 §5.2 逐步补齐；每个工具同时更新本文件与 `COMMANDS.md`。

## 错误约定

- 卫星不存在：`{"ok": false, "error": "not_found", "sat_id": ...}`。
- 同步失败：`sync_status.last_run.status` 为 `retrying` / `abandoned`，并带 `error_code`。
- TLE 过期：`age_hours > 72` 时由上层标记 `degraded`；`> 168` 拒绝用于过境计算。
