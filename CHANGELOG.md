# Changelog

RS-Service 的用户可见变化。版本规则见 [VERSIONING.md](https://github.com/Me106y/SIRIAgent/blob/main/docs/VERSIONING.md)。

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
