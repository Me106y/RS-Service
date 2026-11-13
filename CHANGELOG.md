# Changelog

RS-Service 的用户可见变化。版本规则见 [VERSIONING.md](https://github.com/Me106y/SIRIAgent/blob/main/docs/VERSIONING.md)。

## [1.8.2] - 2026-11-13

### Fixed

- task5（给定星数最小化 ART）的报告不再写「达标面积 0.0%」：首屏 KPI 改为「面积加权 ART 20.6 h」，去掉时间覆盖率；薄弱区表保留「时间覆盖率 + ART」两列。task5 本来就没有面积目标，`qualified_area_pct` 为 `null` 被当成 0.0% 显示，和 task2 之前那个问题同源。

## [1.8.1] - 2026-11-13

### Fixed

- task2（最少星达到重访）报告的「达标面积」旁边补上重访目标：`达标面积（最长重访 ≤ 48 h · 面积 90%）`。之前只写「达标面积（目标 90%）」，24 分钟的目标让 42 颗星也只得到 0.0%，看起来像选星坏了。
- 达标面积 0.0% 时报告追加一条口径说明：单格最长重访间隔含夜间与窗口首尾空档，光学单格下限约 12–19 h，分钟级目标必然为 0；要么改小时级目标、要么换全天候载荷，不要调低面积目标。

## [1.8.0] - 2026-11-12

### Added

- 新增 `GET /api/v1/plans/history`：按任务类型列出历史报告（`?operation=task1&limit=200`），只读 `meta.json`，返回 `runs / total / by_task`，按生成时间倒序；供 SIRIAgent 抽屉的「历史结果」使用。
- `write_report` 额外写 `meta.json`（任务、区域、窗口、星数、KPI、生成时间），并支持自定义范围（`aoi_kind=custom`）。

### Changed

- 报告目录改按任务分层：`reports/imaging-plan/<task>/<run_id>/{plan.json,report.html,meta.json}`，根上 `latest` 软链指向 `<task>/<run_id>`；`GET /plans/{job_id}/report` 改为扫 `<task>/` 找 run，旧 slug 自动归一成 `task1`..`task5`。
- 报告标题与「任务」一栏同时认短 slug（`task1`..`task5`）与旧长 slug，都会显示中文任务名。
- 「覆盖与回放」的回放时钟只显示时间，去掉累计覆盖百分比（`#pct` 与 `cum[]` 一并删除）；推荐计划表的平均/最大覆盖、KPI 条与口径注释保持原样。
- 报告内的「购买本方案」在加载时问一次 SIRIAgent 的购买记录：已买过就显示「已购买」并链到对应购买单，避免同一份报告重复下单。

## [1.7.1] - 2026-11-12

### Fixed

- 单颗卫星 SGP4 传播失败（错误码 6 = 轨道已衰减，如 NORAD 53445 Jilin-1 GF-03D09）不再让整份 imaging-plan 以 `execution_error` 失败：复杂传感器路径按时刻 `valid` 掩码把传播失败时段判为不可覆盖；整颗星全部时刻失败时跳过该星并在 `assumptions` 写明「已从机会计算中跳过」。长窗口（7 天）规划不再被一颗衰减星拖垮。

## [1.7.0] - 2026-11-12

### Added

- 遥感解译新增**图像处理**三算子：灰度图 / Canny 边缘（soft 30/90、normal 50/150、sharp 80/200）/ 降噪（light 高斯 3×3、medium 中值 5×5、strong NL-Means），REST `POST /api/v1/interpret/process-image` 与 MCP 工具 `process_image`；返回结果图路径、算子/档位、尺寸、耗时与 `accuracy_note`。
- 结果落 `reports/interpret/<run_id>/`，文件名 `<原文件名>_<算子>[_<档位>].png`（对齐 SIRIGeo）。

## [1.6.0] - 2026-11-12

### Added

- 遥感解译新增**语义分割**：SparseMask + OpenEarthMap 8 类 ONNX（MIT 代码 / CC BY-NC-SA 权重，限所内），REST `POST /api/v1/interpret/semantic-segmentation` 与 MCP 工具 `segment_semantics`；返回 8 类专题图、半透明叠加图、各类像素与占比、`accuracy_note`。
- 新增依赖 `onnxruntime>=1.18`（CPU）。
- 权重按需拉取：`scripts/fetch_models.py sparsemask`（hf-mirror + SHA256 校验），类别表 `sparsemask_config.json` 入库。

### Changed

- `warmup_safe` 在权重存在时预热语义分割会话。

## [1.5.0] - 2026-11-12

### Added

- 遥感解译新增**目标检测**：YOLOX-S + COCO 80 类官方预训练（Apache-2.0），REST `POST /api/v1/interpret/object-detection` 与 MCP 工具 `detect_objects`；返回标注图、目标数/类别数/置信度统计、检测框列表与 `accuracy_note`。
- 解译路径根从 `workspace/` 扩到 `workspace/` + `demo/`；`/interpret/images` 与 `list_workspace_images` 增加 `kind=demo` 示例图。
- 权重按需拉取：`scripts/fetch_models.py yolox`（ghproxy 镜像 + SHA256 校验），权重不入库。

### Changed

- `warmup_safe` 在权重存在时预热目标检测模型。

## [1.4.2] - 2026-11-11

### Fixed

- 云量过滤不再用一个 AOI 质心点代表整个区域：按 AOI 内最多 4×4 个网格代表点取 Open-Meteo 云量，逐网格剔除 >70% 的访问。修复杭州这类跨 1° 以上区域“西部山区阴雨把东部晴空窗口一起判死”的问题；此前 task1 在 72h→168h 扩窗后出现选中 0 颗、达标面积 0.0%。

## [1.4.1] - 2026-11-10

### Fixed

- task1 自动扩窗不再停在 72h：按「请求窗口 → 72h → 168h」阶梯求解（最多 3 次，最大 7 天）。修复请求 72h 时 `auto_expand` 完全不生效、3 天不达标就判不可行的问题；168h 结果 1 颗 FENGYUN 3D 即可覆盖自定义范围 100%。

## [1.4.0] - 2026-11-10

### Changed

- task4 默认按“给定 N 颗”补满预算（`exact_budget=True`），不再只返回满足 90% 面积目标的最少星数；新增固定 N 的面积分位 MRT 贪心，结果是启发式。
- task4 报告改为自己的 KPI：标题与首屏显示「N% 面积分位 MRT」，并显示「星数预算 / 实际」；不再把达标面积/时间覆盖率当 task4 标题，也不展示时间覆盖率。
- task4 薄弱区只显示位置、面积与 MRT；报告口径说明“覆盖与回放是至少覆盖一次的联合面积，不是 MRT KPI”。

## [1.3.1] - 2026-11-10

### Changed

- 报告“推荐计划”给同一颗星的多段可见窗口加「第 i/n 段」标记，并说明中间空档不是重复选星。
- 报告“薄弱区”不再显示 `330100:-29:-2:0` 这类内部网格 ID，改为“杭州西南 + 经纬度”，并说明是近似方位。

## [1.3.0] - 2026-11-10

### Changed

- 覆盖引擎接入成像门限：非 SAR 最小仰角 ≥10°；光学/高光谱另要求目标地面太阳高度 ≥10°（`RS_OPTICAL_SUN_MIN_DEG`，默认 10）；SAR 不看太阳。夜间/低仰角过境不再计入覆盖，五个选星任务共用。
- task3 在太阳门限下重跑预算敏感性：n=2（ZIYUAN 3-03 + BEIJING-3B）已覆盖 100%；n=3 第三颗改为白天过境的 ZY-1 02E（90 km 幅宽，10:47，二次覆盖），不再选夜航 HJ-2B。

## [1.2.0] - 2026-11-10

### Changed

- task3 从“单格时间覆盖率 ≥ 目标的最大达标面积”改为“给定星数在时间窗内最大化覆盖面积”：不再要求 `temporal_coverage_target_pct`，内部固定 0.001% 的至少覆盖一次阈值，`summary_cards.covered_area_pct` 即覆盖面积，`targets.temporal_coverage_pct`/`area_pct` 为 `null`。
- task3 报告与结果卡改称“覆盖面积”，不再展示时间覆盖率、面积加权 MRT；薄弱区只保留网格与面积。
- 报告回放按“覆盖该网格总时长最多的卫星”着色，修复只显示最先覆盖卫星颜色、其余选中卫星颜色不出现的问题；回放终值与顶部覆盖面积一致。

## [1.1.2] - 2026-11-09

### Changed

- task3 达标面积 0.0% 时，报告隐藏时间覆盖率、面积加权 MRT 与薄弱区，改为说明目标单格覆盖率在窗口内不可达；task3 的判定口径不变。

## [1.1.1] - 2026-11-09

### Changed

- 机会窗口按“质心膨胀视场”做逐星时步预筛，只对可能命中的时步计算网格。task1（杭州 72h / 5km / 10s）机会窗口 150.4s → 2.9s，端到端 185s → 39s；摘要、选星与过境表逐字段一致。
- 新增 `RS_OPPORTUNITY_PRESCREEN=0` 回退开关。

### Fixed

- 机会缓存 key 不再包含每次求解都变的临时目录，同参数二次求解可命中缓存（机会窗口 0s，端到端约 36s）。

## [1.1.0] - 2026-11-09

### Added

- 成图报告在“推荐计划”末尾增加「购买本方案」按钮，链接到 SIRIAgent 的 `/purchase/<run_id>`；`render_report` 新增可选 `run_id` 参数，旧调用不受影响。
- 变化检测：vendor FlickCD（双输入 3 通道、256 滑窗、阈值 0.5），权重入库；GPU 优先 / CPU 回退，最大边 2048、55 秒主动超时，结果写 `reports/interpret/<run_id>/`。
- REST `/api/v1/interpret/images`、`/change-detection` 与 MCP `list_workspace_images`、`detect_change`；路径限定在 SIRIAgent 的 `workspace/`。

## [1.0.0] - 2026-11-07

### Added

- Phase 2 完成基线：65 颗卫星目录与 TLE 日同步、过境/覆盖计算、成图规划 5 任务与四节报告、REST / MCP / CLI。
- 发布自动化：合并到 `main` 后按 `VERSION` 自动打 tag，并双推 GitHub / Gitee。

## [0.1.0] - 2026-11-07

### Added

- 初始版本基线：65 颗卫星目录与 TLE 日同步、过境/覆盖计算、成图规划 5 任务、REST / MCP / CLI。
