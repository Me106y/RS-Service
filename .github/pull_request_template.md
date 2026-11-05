<!-- 标题：<type>(<scope>): <英文祈使句>；正文中文。 -->

## 做了什么

<!-- 一句话，与 PR 标题一致 -->

## 为什么

<!-- 需求文档章节 / issue；不要写“优化体验”这类空话 -->

## 改了哪一层

- [ ] API `api/`
- [ ] MCP `mcp/`
- [ ] 星历 / 同步 `astro/`、`sync/`
- [ ] 数据库 / Alembic 迁移
- [ ] CLI / 部署 / 配置
- [ ] 跨仓：SIRIAgent PR #

## 硬规则自查（碰了哪层勾哪层）

- [ ] 数据库：新 revision；空库 `alembic upgrade head` 通过；未改已合并迁移
- [ ] 同步：成功 / 重试 / `abandoned` 测试覆盖；失败保留上一版数据
- [ ] 接口：REST/MCP 共用 service；`docs/api.md` 与 `COMMANDS.md` 已同步
- [ ] 口径：结果带 `accuracy_note`；非公开卫星未泄漏分辨率 / 幅宽 / `raw_spec`
- [ ] 安全：无 `.env`、DSN 密码、数据库文件入库

## 怎么验证

<!-- 贴命令与结果；改表时补空库升级的输出 -->

```sh
uv run pytest
uv run ruff check .
```

## 风险与回滚

<!-- 要重启哪个 systemd 单元；出问题怎么撤回 -->

---
<!-- agent 提的 PR：在最上方第一行加 This is by an automated agent, not a human. -->
