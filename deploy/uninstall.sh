#!/bin/sh
set -eu

INSTALL_DIR="/opt/slowlink"
APP_CONTAINER="slowlink_app"
REDIS_CONTAINER="slowlink_redis"
CADDY_CONTAINER="slowlink_caddy"
WATCHDOG_SERVICE="slowlink-watchdog.service"
COMPOSE_PROJECT="slowlink"
PURGE=0

container_is_slowlink_service() {
  container_name=$1
  expected_service=$2
  actual_project=$(docker inspect "$container_name" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)
  actual_service=$(docker inspect "$container_name" --format '{{index .Config.Labels "com.docker.compose.service"}}' 2>/dev/null || true)
  [ "$actual_project" = "$COMPOSE_PROJECT" ] && [ "$actual_service" = "$expected_service" ]
}

remove_slowlink_container() {
  container_name=$1
  expected_service=$2
  docker inspect "$container_name" >/dev/null 2>&1 || return 0
  if ! container_is_slowlink_service "$container_name" "$expected_service"; then
    printf '[卸载警告] 同名容器不属于 SlowLink，未删除：%s\n' "$container_name" >&2
    return 1
  fi
  docker rm -f "$container_name" >/dev/null 2>&1 || {
    printf '[卸载警告] 容器删除失败：%s\n' "$container_name" >&2
    return 1
  }
}

volume_is_slowlink_owned() {
  volume_name=$1
  expected_volume=$2
  actual_project=$(docker volume inspect "$volume_name" --format '{{index .Labels "com.docker.compose.project"}}' 2>/dev/null || true)
  actual_volume=$(docker volume inspect "$volume_name" --format '{{index .Labels "com.docker.compose.volume"}}' 2>/dev/null || true)
  [ "$actual_project" = "$COMPOSE_PROJECT" ] && [ "$actual_volume" = "$expected_volume" ]
}

remove_slowlink_volume() {
  volume_name=$1
  expected_volume=$2
  docker volume inspect "$volume_name" >/dev/null 2>&1 || return 0
  if ! volume_is_slowlink_owned "$volume_name" "$expected_volume"; then
    printf '[卸载警告] 同名数据卷不属于 SlowLink，未删除：%s\n' "$volume_name" >&2
    return 1
  fi
  docker volume rm "$volume_name" >/dev/null 2>&1 || {
    printf '[卸载警告] 数据卷删除失败：%s\n' "$volume_name" >&2
    return 1
  }
}

if [ "${1:-}" = "--purge" ]; then
  PURGE=1
elif [ "$#" -gt 0 ]; then
  printf '[卸载失败] 未知参数：%s\n' "$1" >&2
  exit 1
fi

[ "$(id -u)" -eq 0 ] || {
  printf '[卸载失败] 请使用 root 或 sudo 运行\n' >&2
  exit 1
}

if [ "$PURGE" -eq 1 ]; then
  cat > /dev/tty <<'EOF'
警告：此操作会删除 SlowLink 程序、配置、Telegram Session、Redis 数据和数据库。
EOF
  printf '请输入 PURGE 确认：' > /dev/tty
  answer=""
  IFS= read -r answer < /dev/tty || answer=""
  [ "$answer" = "PURGE" ] || {
    printf '已取消彻底删除。\n' > /dev/tty
    exit 0
  }
  [ "$INSTALL_DIR" = "/opt/slowlink" ] || {
    printf '[卸载失败] 安装目录安全检查失败\n' >&2
    exit 1
  }

  systemctl disable --now "$WATCHDOG_SERVICE" >/dev/null 2>&1 || true
  rm -f -- "/etc/systemd/system/$WATCHDOG_SERVICE"
  systemctl daemon-reload

  redis_data_volume=""
  caddy_data_volume=""
  caddy_config_volume=""
  if docker inspect "$REDIS_CONTAINER" >/dev/null 2>&1; then
    redis_data_volume=$(docker inspect "$REDIS_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)
  fi
  if docker inspect "$CADDY_CONTAINER" >/dev/null 2>&1; then
    caddy_data_volume=$(docker inspect "$CADDY_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)
    caddy_config_volume=$(docker inspect "$CADDY_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)
  fi
  purge_failed=0
  if [ -f "$INSTALL_DIR/deploy/docker-compose.yml" ]; then
    docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" --profile https stop caddy >/dev/null 2>&1 || true
    docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" --profile https rm -f caddy >/dev/null 2>&1 || true
    docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" stop app >/dev/null 2>&1 || true
    docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" rm -f app >/dev/null 2>&1 || true
  fi
  remove_slowlink_container "$APP_CONTAINER" app || purge_failed=1
  remove_slowlink_container "$CADDY_CONTAINER" caddy || purge_failed=1
  remove_slowlink_container "$REDIS_CONTAINER" redis || purge_failed=1
  for redis_volume in "$redis_data_volume" slowlink_redis_data; do
    [ -n "$redis_volume" ] || continue
    remove_slowlink_volume "$redis_volume" redis_data || purge_failed=1
  done
  for caddy_volume in "$caddy_data_volume" slowlink_caddy_data; do
    [ -n "$caddy_volume" ] || continue
    remove_slowlink_volume "$caddy_volume" caddy_data || purge_failed=1
  done
  for caddy_volume in "$caddy_config_volume" slowlink_caddy_config; do
    [ -n "$caddy_volume" ] || continue
    remove_slowlink_volume "$caddy_volume" caddy_config || purge_failed=1
  done
  if [ "$purge_failed" -ne 0 ]; then
    printf '[卸载失败] 部分同名资源归属不明或删除失败，安装目录已保留以便检查。\n' >&2
    exit 1
  fi
  rm -rf -- "$INSTALL_DIR"
  printf 'SlowLink 已彻底删除。\n'
  exit 0
fi

# 保留数据卸载
systemctl disable --now "$WATCHDOG_SERVICE" >/dev/null 2>&1 || true
rm -f -- "/etc/systemd/system/$WATCHDOG_SERVICE"
systemctl daemon-reload
if [ -f "$INSTALL_DIR/deploy/docker-compose.yml" ]; then
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" --profile https stop caddy >/dev/null 2>&1 || true
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" --profile https rm -f caddy >/dev/null 2>&1 || true
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" stop app >/dev/null 2>&1 || true
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" rm -f app >/dev/null 2>&1 || true
fi
uninstall_failed=0
remove_slowlink_container "$APP_CONTAINER" app || uninstall_failed=1
remove_slowlink_container "$CADDY_CONTAINER" caddy || uninstall_failed=1
if [ "$uninstall_failed" -ne 0 ]; then
  printf '[卸载失败] 应用或 HTTPS 代理未能安全删除，Redis 和数据未受影响。\n' >&2
  exit 1
fi
printf 'SlowLink 程序与 HTTPS 代理已卸载，已保留配置、Telegram Session、Redis 数据和数据库，HTTPS 证书也已保留。\n'
printf '保留目录：%s\n' "$INSTALL_DIR"
