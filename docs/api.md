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
| POST | `/plans/{job_id}/report` | 用已完成结果渲染四节 HTML，返回 `reports/imaging-plan/<task>/<job_id>/...` 相对路径 |
| GET | `/plans/{job_id}/report` | 报告 HTML 本体（按 `run_id` 扫 `<task>/` 子目录） |
| GET | `/plans/history` | 历史报告列表（只读 `meta.json`），`?operation=task1&limit=200` |

任务：`task1` 最少星达覆盖率（面积口径）、`task2` 最少星达重访、`task3` 给定星数最大化覆盖面积（面积口径）、
`task4` 给定星数最小化面积分位 MRT、`task5` 给定星数最小化 ART。

- AOI 支持省/市/区县 adcode 或 `POLYGON` WKT；只允许浙江省内。
- 目录 65 颗（61 光学 + 4 高光谱）；SAR 缺原生入射角带，暂不参与。
- 云量默认按 AOI 内网格代表点取 Open-Meteo 逐小时 `cloud_cover`，逐网格剔除 `> 70%` 的访问（采样点最多 4×4）；不再用 AOI 质心单点代表全区域。取不到时不过滤并在 assumptions 说明。
- 成像门限：非 SAR 最小仰角 ≥10°；光学/高光谱另要求目标地面太阳高度 ≥10°（`RS_OPTICAL_SUN_MIN_DEG`，默认 10）；SAR 不看太阳。夜间/低仰角窗口不进入覆盖与选星，结果 `imaging_constraints` 与 assumptions 会记录。
- `task1` 按面积口径：至少成像一次即视为覆盖（内部时间阈值固定 0.001%），默认面积目标 90%，`temporal_coverage_target_pct` 会被忽略；结果 `targets.area_pct` 是面积目标、`targets.temporal_coverage_pct` 为 `null`。报告不展示单格时间覆盖率与薄弱区。
- `task3` 按面积口径：星数固定，时间窗内至少覆盖一次即计入覆盖面积；内部时间阈值固定 0.001%，不再要求也不再使用 `temporal_coverage_target_pct`。结果 `summary_cards.covered_area_pct`（兼容字段 `qualified_area_pct`）就是覆盖面积，`targets.temporal_coverage_pct` / `targets.area_pct` 为 `null`。报告不展示时间覆盖率/MRT，薄弱区只列网格与面积。
- `task4` 默认 `exact_budget=True`：给定 N 颗就补满 N 颗，按 N% 面积分位 MRT 做固定预算贪心；报告首屏显示「N% 面积分位 MRT」与「星数预算 / 实际」，不展示时间覆盖率；覆盖与回放显示联合覆盖，不是 MRT KPI。
- `auto_expand` 只允许 `task1`：先按请求窗口算，不达标再按 72h → 168h 阶梯扩窗（最多 3 次求解，最大 7 天；超过 7 天的 TLE 按口径拒绝过境）。请求 72h 时也会继续扩到 168h。结果 `window` 返回请求/实际窗口和 `expanded`，报告按实际窗口展示。
- TLE 年龄按 `tle_record.epoch_utc` 实时计算：>72h 标 degraded，全部候选 >168h 时返回 `tle_stale`；`plan-options.tle` 暴露 `max_age_hours` / `degraded` / `stale`。
- `rs-service-sync.timer` 每天 03:00 同步；TLE 过期时先跑一次 `rs-service sync`。
- 任务状态在进程内；重启丢任务，报告文件保留。
- 报告落盘：`reports/imaging-plan/<task>/<run_id>/{plan.json,report.html,meta.json}`，`<task>` 是 `task1`..`task5`（旧 slug 自动归一）；根上 `latest` 软链指向最近一份（`<task>/<run_id>`）。`meta.json` 是历史列表要用的精简元数据（任务、区域、窗口、星数、KPI、生成时间），避免为了列表去读几百 KB 的 `plan.json`。
- `GET /plans/history` 返回 `{ok, root, runs[], total, by_task}`；`runs` 按 `created_at` 倒序，每项含 `run_id / task / task_label / aoi_kind(city|custom) / area_name / window_start / window_end / sat_count / kpi{key,label,value,unit} / feasible / created_at / relative_report / relative_plan / has_report`。`by_task` 统计全部任务（不受 `operation` 过滤影响），方便前端渲染组头；`limit` 上限 500。
- KPI 口径随任务：`task1` 达标面积（`qualified_area_pct`）、`task2` 达标面积 = 面积中「单格最长重访间隔 ≤ 目标」的占比（报告标签带重访目标，MRT 含夜间与窗口首尾空档）、`task3` 覆盖面积、`task4` N% 面积分位 MRT（小时）、`task5` 面积加权 ART（报告首屏就是「面积加权 ART」，不出现达标面积/时间覆盖率，薄弱区保留时间覆盖率 + ART 两列）。
- 报告「覆盖与回放」只显示回放时间（不再显示累计覆盖百分比）；购买按钮在报告加载时问一次 SIRIAgent 的 `/api/purchases`，已买过就显示「已购买」并链到对应购买单。

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
| `list_satellites` | `category?`, `orbit?` | 65 颗摘要（norad/name/category/orbit/payload_public） |
| `get_satellite` | `sat_id` | 目录 + 传感器摘要 + TLE 年龄 |
| `next_passes` | `sat_id`, `hours?`, `lat?`, `lon?`, `min_elev?` | 过境窗口（AOS/LOS/TCA/最大仰角/太阳高度/光学可用） |
| `ground_track` | `sat_id`, `hours?`, `step_s?` | 星下点轨迹 |
| `list_positions` | — | 65 颗当前星下点（批量） |
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
