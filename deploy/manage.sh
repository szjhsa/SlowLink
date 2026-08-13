#!/bin/sh
set -eu

REPO="szjhsa/SlowLink"
INSTALL_DIR="/opt/slowlink"
APP_CONTAINER="slowlink_app"
REDIS_CONTAINER="slowlink_redis"
WATCHDOG_SERVICE="slowlink-watchdog.service"
BACKUP_DIR="/var/backups/slowlink"

if [ ! -r "$INSTALL_DIR/scripts/distribution_lib.sh" ]; then
  printf '[管理失败] 缺少 %s/scripts/distribution_lib.sh\n' "$INSTALL_DIR" >&2
  exit 1
fi
# shellcheck disable=SC1091
. "$INSTALL_DIR/scripts/distribution_lib.sh"

usage() {
  cat <<'EOF'
用法：sudo /opt/slowlink/deploy/manage.sh COMMAND

  status     查看版本、容器、Redis、监听和 watchdog 状态
  logs       实时查看 slowlink_app 日志
  restart    只重启 slowlink_app
  update     更新到最新 GitHub Release
  web        切换域名 HTTPS 或 IP + 端口 HTTP
  backup     备份配置、Telegram Session 和 Redis 数据
  uninstall  卸载程序并保留配置和数据
  purge      彻底删除 SlowLink 自有资源
EOF
}

redis_value() {
  redis_key=$1
  docker exec "$REDIS_CONTAINER" redis-cli --raw GET "$redis_key" 2>/dev/null || printf '不可用'
}

show_status() {
  version=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || printf '未知')
  SLOWLINK_WEB_MODE=$(read_web_mode)
  SLOWLINK_WEB_PORT=$(read_web_port)
  printf '版本：%s\n' "$version"
  printf '网页模式：%s\n' "$SLOWLINK_WEB_MODE"
  printf '网页端口：%s\n' "$SLOWLINK_WEB_PORT"
  printf '网页地址：%s\n' "$(web_access_url)"
  if [ "$SLOWLINK_WEB_MODE" = "https" ]; then
    printf 'HTTPS 代理：%s\n' "$(docker inspect "$CADDY_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || printf '未运行')"
  fi
  if docker inspect "$APP_CONTAINER" >/dev/null 2>&1; then
    docker inspect "$APP_CONTAINER" --format '应用：{{.State.Status}} / {{if .State.Health}}{{.State.Health.Status}}{{else}}无健康检查{{end}}，重启={{.RestartCount}}，OOM={{.State.OOMKilled}}'
    docker stats --no-stream --format '资源：CPU {{.CPUPerc}}，内存 {{.MemUsage}}' "$APP_CONTAINER" 2>/dev/null || true
  else
    printf '应用：未安装或未运行\n'
  fi
  if docker inspect "$REDIS_CONTAINER" >/dev/null 2>&1; then
    docker inspect "$REDIS_CONTAINER" --format 'Redis：{{.State.Status}} / {{if .State.Health}}{{.State.Health.Status}}{{else}}无健康检查{{end}}'
    printf '监听期望：%s\n' "$(redis_value listener_desired_state)"
    printf '监听状态：%s\n' "$(redis_value bot_status)"
    printf 'Telegram 登录：%s\n' "$(redis_value tg_logged_in)"
    printf '转发目标：%s\n' "$(redis_value target_chat)"
    flow_stats=$(redis_value listener_flow_stats)
    printf '最近消息流：%s\n' "${flow_stats:-暂无}"
  else
    printf 'Redis：未运行\n'
  fi
  printf 'Telegram Session 文件：%s 个\n' "$(find "$INSTALL_DIR/data/sessions" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')"
  printf 'CPU watchdog：%s\n' "$(systemctl is-active "$WATCHDOG_SERVICE" 2>/dev/null || printf 'inactive')"
}

restart_app() {
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" restart app || die "slowlink_app 重启失败"
  if ! wait_for_app_health 90; then
    show_diagnostics
    die "slowlink_app 重启后未通过健康检查"
  fi
  log "slowlink_app 已重启并通过健康检查"
}

configure_web_access() {
  original_web_mode=$(read_web_mode)
  original_bind_host=$(read_web_bind_host)
  original_web_port=$(read_web_port)
  original_domain=$(read_web_domain)

  while true; do
    cat > /dev/tty <<'EOF'
网页访问方式
1.域名 HTTPS（推荐）
2.IP + 自定义端口 HTTP
0.取消
EOF
    printf '请选择：' > /dev/tty
    web_choice=""
    IFS= read -r web_choice < /dev/tty || web_choice=""
    case "$web_choice" in
      1)
        if [ -n "$original_domain" ]; then
          printf '域名 [当前 %s]：' "$original_domain" > /dev/tty
        else
          printf '请输入已解析到本机的域名：' > /dev/tty
        fi
        selected_domain=""
        IFS= read -r selected_domain < /dev/tty || selected_domain=""
        selected_domain=${selected_domain:-$original_domain}
        if ! validate_web_domain "$selected_domain"; then
          printf '[输入错误] 请输入有效域名，例如 slowlink.example.com。\n' > /dev/tty
          continue
        fi
        selected_mode=https
        selected_port=$original_web_port
        selected_bind_host=127.0.0.1
        validate_https_app_port "$selected_port" || die "当前应用端口为 80/443，请先切到 HTTP 并改用其他端口"
        assert_https_ports_available || die "HTTPS 端口预检失败"
        break
        ;;
      2)
        selected_mode=http
        selected_domain=""
        selected_bind_host=0.0.0.0
        while true; do
          printf '网页端口 [当前 %s]：' "$original_web_port" > /dev/tty
          selected_port=""
          IFS= read -r selected_port < /dev/tty || selected_port=""
          selected_port=${selected_port:-$original_web_port}
          if ! validate_web_port "$selected_port"; then
            printf '[输入错误] 请输入 1-65535 的端口。\n' > /dev/tty
            continue
          fi
          assert_web_port_available "$selected_port" && break
        done
        break
        ;;
      0) log "已取消网页模式切换"; return 0 ;;
      *) printf '[输入错误] 请输入 1、2 或 0。\n' > /dev/tty ;;
    esac
  done

  if (
    save_web_access "$selected_mode" "$selected_port" "$selected_domain" &&
    SLOWLINK_WEB_MODE=$selected_mode &&
    SLOWLINK_BIND_HOST=$selected_bind_host &&
    SLOWLINK_WEB_PORT=$selected_port &&
    SLOWLINK_DOMAIN=$selected_domain &&
    export SLOWLINK_WEB_MODE SLOWLINK_BIND_HOST SLOWLINK_WEB_PORT SLOWLINK_DOMAIN &&
    docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" up -d --no-deps "$APP_SERVICE" &&
    wait_for_app_health 90 &&
    ensure_web_proxy &&
    verify_installation
  ); then
    log "网页访问方式已切换：$(web_access_url)"
    return 0
  fi

  warn "网页访问方式切换失败，正在恢复原配置"
  save_web_access "$original_web_mode" "$original_web_port" "$original_domain" "$original_bind_host"
  SLOWLINK_WEB_MODE=$original_web_mode
  SLOWLINK_BIND_HOST=$original_bind_host
  SLOWLINK_WEB_PORT=$original_web_port
  SLOWLINK_DOMAIN=$original_domain
  export SLOWLINK_WEB_MODE SLOWLINK_BIND_HOST SLOWLINK_WEB_PORT SLOWLINK_DOMAIN
  if docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" up -d --no-deps "$APP_SERVICE" && wait_for_app_health 90 && ensure_web_proxy && verify_installation; then
    die "切换失败，已恢复原网页访问方式"
  fi
  show_diagnostics
  die "切换失败，且自动恢复未通过验证"
}

download_installer() {
  installer_output=$1
  installer_url="https://raw.githubusercontent.com/$REPO/main/deploy/install.sh"
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" "$installer_url" -o "$installer_output"
  else
    curl -fsSL "$installer_url" -o "$installer_output"
  fi
}

backup_runtime() {
  timestamp=$(date '+%Y%m%d_%H%M%S')
  mkdir -p "$BACKUP_DIR"
  backup_stage=$(mktemp -d /tmp/slowlink-backup.XXXXXX)
  trap 'rm -rf -- "$backup_stage"' 0
  mkdir -p "$backup_stage/runtime"

  if [ -f "$INSTALL_DIR/.env" ]; then
    cp -a "$INSTALL_DIR/.env" "$backup_stage/runtime/.env"
  fi
  if [ -d "$INSTALL_DIR/data" ]; then
    cp -a "$INSTALL_DIR/data" "$backup_stage/runtime/data"
  fi

  if docker inspect "$REDIS_CONTAINER" >/dev/null 2>&1; then
    log "请求 Redis 生成持久化快照"
    docker exec "$REDIS_CONTAINER" redis-cli BGSAVE >/dev/null || die "Redis BGSAVE 失败"
    backup_wait=0
    while [ "$backup_wait" -lt 60 ]; do
      progress=$(docker exec "$REDIS_CONTAINER" redis-cli --raw INFO persistence 2>/dev/null | tr -d '\r' | sed -n 's/^rdb_bgsave_in_progress://p')
      status=$(docker exec "$REDIS_CONTAINER" redis-cli --raw INFO persistence 2>/dev/null | tr -d '\r' | sed -n 's/^rdb_last_bgsave_status://p')
      if [ "$progress" = "0" ] && [ "$status" = "ok" ]; then
        break
      fi
      sleep 1
      backup_wait=$((backup_wait + 1))
    done
    [ "$backup_wait" -lt 60 ] || die "Redis 快照在 60 秒内未完成"
    docker cp "$REDIS_CONTAINER:/data/dump.rdb" "$backup_stage/runtime/redis_dump.rdb" >/dev/null || die "复制 Redis 快照失败"
  else
    warn "Redis 容器未运行，本次备份不包含 Redis 快照"
  fi

  {
    printf 'created_at=%s\n' "$(date -Iseconds)"
    printf 'version=%s\n' "$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || printf '未知')"
    printf 'app_container=%s\n' "$(docker inspect "$APP_CONTAINER" --format '{{.Id}}' 2>/dev/null || printf 'missing')"
    printf 'redis_container=%s\n' "$(docker inspect "$REDIS_CONTAINER" --format '{{.Id}}' 2>/dev/null || printf 'missing')"
  } > "$backup_stage/runtime/MANIFEST.txt"

  backup_file="$BACKUP_DIR/slowlink_backup_$timestamp.tar.gz"
  tar -C "$backup_stage/runtime" -czf "$backup_file" . || die "创建备份压缩包失败"
  chmod 600 "$backup_file"
  log "备份完成：$backup_file"
}

require_root
[ "$#" -ge 1 ] || { usage; exit 1; }

case "$1" in
  status)
    show_status
    ;;
  logs)
    exec docker logs -f --tail 100 "$APP_CONTAINER"
    ;;
  restart)
    restart_app
    ;;
  update)
    update_installer=$(mktemp /tmp/slowlink-update.XXXXXX)
    trap 'rm -f -- "$update_installer"' 0
    download_installer "$update_installer" || die "下载安装脚本失败"
    sh "$update_installer" --update
    ;;
  web)
    configure_web_access
    ;;
  backup)
    backup_runtime
    ;;
  uninstall)
    exec "$INSTALL_DIR/deploy/uninstall.sh"
    ;;
  purge)
    exec "$INSTALL_DIR/deploy/uninstall.sh" --purge
    ;;
  --help|-h|help)
    usage
    ;;
  *)
    die "未知命令：$1"
    ;;
esac
