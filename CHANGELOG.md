# Changelog

RS-Service 的用户可见变化。版本规则见 [VERSIONING.md](https://github.com/Me106y/SIRIAgent/blob/main/docs/VERSIONING.md)。

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
