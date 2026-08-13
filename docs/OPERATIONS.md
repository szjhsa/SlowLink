# SlowLink 运维说明

## 安装与更新

```bash
curl -fsSL https://raw.githubusercontent.com/szjhsa/SlowLink/main/install.sh | sudo bash
```

脚本从 `/dev/tty` 读取菜单输入，适用于管道运行。安装目录固定为 `/opt/slowlink`，支持 Ubuntu 和 Debian，并在缺少 Docker 时自动安装 Docker Engine 与 Docker Compose 插件。

更新流程从 GitHub 获取最新正式 Release，下载 full 包和 `SHA256SUMS.txt`，验证 SHA-256 和归档内容后才复制程序文件。更新应用时分开执行：

```bash
docker compose build --no-cache app
docker compose up -d --no-deps app
```

更新不会执行 `docker compose down`，不会停止 `slowlink_redis`，不会覆盖 `.env`、`data`、Telegram Session、Redis 数据或用户配置。HTTPS 模式下，域名未变化且 `slowlink_caddy` 正常运行时不会重启 Caddy。

安装支持域名 HTTPS 和 IP + 自定义端口 HTTP。HTTPS 模式由 Caddy 暴露 80/443，应用管理端口只绑定 `127.0.0.1`；HTTP 模式保持原有自定义端口行为。

## 日常管理

```bash
sudo /opt/slowlink/manage.sh status
sudo /opt/slowlink/manage.sh logs
sudo /opt/slowlink/manage.sh restart
sudo /opt/slowlink/manage.sh update
sudo /opt/slowlink/manage.sh web
sudo /opt/slowlink/manage.sh backup
```

`status` 显示版本、应用健康状态、Redis 状态、监听期望状态、监听运行状态、Telegram 登录状态、转发目标、Session 文件数量和 CPU watchdog 状态。

`restart` 只重启 `slowlink_app` 并等待健康检查。`logs` 只跟踪应用容器日志。`web` 可切换 HTTPS/HTTP，失败时恢复原访问模式、绑定地址、端口和域名。

## 备份

`manage.sh backup` 将备份写入：

```text
/var/backups/slowlink/slowlink_backup_YYYYMMDD_HHMMSS.tar.gz
```

备份内容包括存在的 `.env`、`data`、Telegram Session、Redis `dump.rdb` 和版本/容器清单。Redis 使用 `BGSAVE` 完成持久化后再复制快照。备份文件权限设为 `600`。

## 卸载

```bash
sudo /opt/slowlink/manage.sh uninstall
```

普通卸载只移除 `slowlink_app`、`slowlink_caddy` 和 CPU watchdog，保留配置、Telegram Session、Redis 容器、Redis 数据卷、数据库和 Caddy 证书卷。

```bash
sudo /opt/slowlink/manage.sh purge
```

彻底删除会先要求从 `/dev/tty` 输入完全一致的 `PURGE`。确认前不会停止任何服务。确认后只删除 SlowLink 自有应用/Caddy/Redis 容器、已验证的数据卷、watchdog 和 `/opt/slowlink`。

## 健康检查与诊断

安装、更新和重启最多等待 90 秒。失败时脚本会用中文输出以下诊断：

- Docker Compose 配置检查结果
- `slowlink_app` 容器状态、健康状态、OOM 状态和错误信息
- 最近 120 行应用日志
- HTTPS 模式下的 Caddy 状态和最近 80 行日志

手动检查：

```bash
port=$(sed -n 's/^SLOWLINK_WEB_PORT=//p' /opt/slowlink/.env | tail -n 1)
curl -fsS "http://127.0.0.1:${port:-8080}/health"
docker inspect slowlink_app --format '{{.State.Health.Status}}'
systemctl status slowlink-watchdog.service
```

## CPU Watchdog 与监听恢复

`slowlink-watchdog.service` 继续使用现有 `ops/slowlink_watchdog.sh`。只有应用容器连续达到配置的高 CPU 条件才会重启 `slowlink_app`，不会重启 Redis 或其他服务。

监听期望状态保存在 Redis 的 `listener_desired_state`。应用容器启动后由现有业务代码读取该状态并决定是否恢复监听，安装与更新脚本不修改这个键。

## 敏感数据

禁止向 Git 提交 `.env`、密码、Token、API Hash、Telegram Session、数据库、Redis 数据、日志和备份。Release ZIP 使用显式白名单生成，并在构建和安装两端检查禁止路径。
