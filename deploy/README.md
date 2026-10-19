# 部署（用户级 systemd）

同步任务每天 03:00（Asia/Shanghai）跑一次；失败最多重试 3 次，当天 `abandoned`，次日自动恢复。

```bash
mkdir -p ~/.config/systemd/user
cp deploy/rs-service-sync.service deploy/rs-service-sync.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now rs-service-sync.timer

# 查看
systemctl --user list-timers rs-service-sync.timer
journalctl --user -u rs-service-sync.service -n 50
```

首次手动跑一次：

```bash
cd "/home/siri/桌面/RS-Service"
.venv/bin/rs-service init
.venv/bin/rs-service sync
```

## 注意

- `WorkingDirectory` 与 `ExecStart` 使用 `%h/桌面/RS-Service`，换路径时同步改两个文件。
- 数据库在 `~/.local/share/rs-service/rs.db`，不在仓库。
- `Persistent=true` 会在关机错过 03:00 后补跑一次。
- 若本机需要代理访问 CelesTrak，在 service 里加 `Environment=RS_HTTP_PROXY=http://127.0.0.1:7890`。
