#!/bin/sh
set -eu

REPO="suzijin876-lgtm/slowlink"
INSTALL_DIR="/opt/slowlink"
REQUESTED_VERSION=""
REQUESTED_PORT=""
REQUESTED_DOMAIN=""
FORCE_HTTP=0
UPDATE_ONLY=0
SHOW_MENU=0
TMP_DIR=""
BOOTSTRAP_LIB=""
KEEP_TMP_DIR=0

[ "$#" -eq 0 ] && SHOW_MENU=1

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || pwd)
if [ -f "$SCRIPT_DIR/VERSION" ] && [ -r "$SCRIPT_DIR/scripts/distribution_lib.sh" ]; then
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/scripts/distribution_lib.sh"
else
  BOOTSTRAP_LIB=$(mktemp /tmp/slowlink-distribution-lib.XXXXXX)
  curl -fsSL "https://raw.githubusercontent.com/$REPO/main/scripts/distribution_lib.sh" -o "$BOOTSTRAP_LIB" || {
    printf '[SlowLink 失败] 下载分发辅助脚本失败\n' >&2
    exit 1
  }
  # shellcheck disable=SC1090
  . "$BOOTSTRAP_LIB"
fi

cleanup() {
  if [ "$KEEP_TMP_DIR" -ne 1 ] && [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf -- "$TMP_DIR"
  fi
  if [ -n "$BOOTSTRAP_LIB" ] && [ -f "$BOOTSTRAP_LIB" ]; then
    rm -f -- "$BOOTSTRAP_LIB"
  fi
}

trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM

usage() {
  cat <<'EOF'
用法：sudo sh install.sh [--version 1.39.10] [--domain DOMAIN | --http] [--port 8080] [--update]

  --version VERSION  安装指定 GitHub Release
  --domain DOMAIN    使用域名 HTTPS，例如 slowlink.example.com
  --http             使用 IP + 自定义端口 HTTP
  --port PORT        设置应用宿主机端口，默认 8080
  --update           保留配置和数据并更新到最新版本
  --uninstall        卸载程序并保留配置和数据
  --purge            进入彻底删除确认流程
  --help             显示帮助
EOF
}

run_installed_uninstall() {
  uninstall_mode=$1
  if [ ! -f "$INSTALL_DIR/uninstall.sh" ]; then
    printf '[提示] 尚未检测到已安装的 SlowLink。\n' > /dev/tty
    return 1
  fi
  if [ "$uninstall_mode" = "purge" ]; then
    sh "$INSTALL_DIR/uninstall.sh" --purge
  else
    sh "$INSTALL_DIR/uninstall.sh"
  fi
}

uninstall_menu() {
  while true; do
    cat > /dev/tty <<'EOF'
卸载方式
1.卸载程序但保留配置、Telegram Session、Redis 数据和数据库
2.彻底删除
0.返回
EOF
    printf '请选择：' > /dev/tty
    choice=""
    IFS= read -r choice < /dev/tty || choice=""
    case "$choice" in
      1) run_installed_uninstall preserve && exit 0 ;;
      2) run_installed_uninstall purge && exit 0 ;;
      0) return ;;
      *) printf '[输入错误] 请输入 1、2 或 0。\n' > /dev/tty ;;
    esac
  done
}

main_menu() {
  while true; do
    cat > /dev/tty <<'EOF'
SlowLink 管理
1.安装
2.更新到最新版本
3.卸载
0.退出
EOF
    printf '请选择：' > /dev/tty
    choice=""
    IFS= read -r choice < /dev/tty || choice=""
    case "$choice" in
      1) UPDATE_ONLY=0; return ;;
      2)
        if [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
          printf '[提示] 尚未检测到安装，请先选择 1 安装。\n' > /dev/tty
        else
          UPDATE_ONLY=1
          return
        fi
        ;;
      3) uninstall_menu ;;
      0) printf '已退出。\n' > /dev/tty; exit 0 ;;
      *) printf '[输入错误] 请输入 1、2、3 或 0。\n' > /dev/tty ;;
    esac
  done
}

select_web_port() {
  current_port=$(read_web_port)
  if [ -n "$REQUESTED_PORT" ]; then
    validate_web_port "$REQUESTED_PORT" || die "网页端口无效：$REQUESTED_PORT，请输入 1-65535"
    assert_web_port_available "$REQUESTED_PORT" || die "请通过 --port 指定其他空闲端口"
    selected_port=$REQUESTED_PORT
  elif [ "$UPDATE_ONLY" -eq 1 ]; then
    assert_web_port_available "$current_port" || die "已保存的网页端口不可用，请通过 --port 指定其他端口"
    selected_port=$current_port
  elif [ "$SHOW_MENU" -eq 1 ]; then
    suggested_port=$(find_available_port "$current_port" || true)
    [ -n "$suggested_port" ] || die "未找到可用网页端口"
    while true; do
      printf '网页端口 [默认 %s]：' "$suggested_port" > /dev/tty
      selected_port=""
      IFS= read -r selected_port < /dev/tty || selected_port=""
      selected_port=${selected_port:-$suggested_port}
      if ! validate_web_port "$selected_port"; then
        printf '[输入错误] 请输入 1-65535 的端口。\n' > /dev/tty
        continue
      fi
      if assert_web_port_available "$selected_port"; then
        break
      fi
      suggested_port=$(find_available_port "$((selected_port + 1))" || true)
      [ -n "$suggested_port" ] || die "未找到可用网页端口"
    done
  else
    assert_web_port_available "$current_port" || die "默认端口 $current_port 已被占用，请使用 --port 指定其他端口"
    selected_port=$current_port
  fi
  SLOWLINK_WEB_PORT=$selected_port
  export SLOWLINK_WEB_PORT
  log "网页端口：$SLOWLINK_WEB_PORT"
}

select_web_access() {
  current_web_mode=$(read_web_mode)
  current_web_domain=$(read_web_domain)

  if [ -n "$REQUESTED_DOMAIN" ]; then
    validate_web_domain "$REQUESTED_DOMAIN" || die "HTTPS 域名无效：$REQUESTED_DOMAIN"
    SLOWLINK_WEB_MODE=https
    SLOWLINK_DOMAIN=$REQUESTED_DOMAIN
  elif [ "$FORCE_HTTP" -eq 1 ]; then
    SLOWLINK_WEB_MODE=http
    SLOWLINK_DOMAIN=""
  elif [ "$UPDATE_ONLY" -eq 1 ]; then
    SLOWLINK_WEB_MODE=$current_web_mode
    SLOWLINK_DOMAIN=$current_web_domain
    if [ "$SLOWLINK_WEB_MODE" = "https" ]; then
      validate_web_domain "$SLOWLINK_DOMAIN" || die "已保存的 HTTPS 域名无效，请通过 --domain 重新指定"
    fi
  elif [ "$SHOW_MENU" -eq 1 ]; then
    while true; do
      cat > /dev/tty <<'EOF'
网页访问方式
1.域名 HTTPS（推荐）
2.IP + 自定义端口 HTTP
EOF
      printf '请选择：' > /dev/tty
      web_choice=""
      IFS= read -r web_choice < /dev/tty || web_choice=""
      case "$web_choice" in
        1)
          printf '请输入已解析到本机的域名：' > /dev/tty
          SLOWLINK_DOMAIN=""
          IFS= read -r SLOWLINK_DOMAIN < /dev/tty || SLOWLINK_DOMAIN=""
          if validate_web_domain "$SLOWLINK_DOMAIN"; then
            SLOWLINK_WEB_MODE=https
            break
          fi
          printf '[输入错误] 请输入有效域名，例如 slowlink.example.com。\n' > /dev/tty
          ;;
        2)
          SLOWLINK_WEB_MODE=http
          SLOWLINK_DOMAIN=""
          break
          ;;
        *) printf '[输入错误] 请输入 1 或 2。\n' > /dev/tty ;;
      esac
    done
  else
    SLOWLINK_WEB_MODE=http
    SLOWLINK_DOMAIN=""
  fi

  select_web_port
  if [ "$SLOWLINK_WEB_MODE" = "https" ]; then
    validate_https_app_port "$SLOWLINK_WEB_PORT" || die "HTTPS 模式下应用端口不能使用 80 或 443，请通过 --port 指定其他端口"
    assert_https_ports_available || die "HTTPS 端口预检失败"
    SLOWLINK_BIND_HOST=127.0.0.1
    log "网页模式：域名 HTTPS（$SLOWLINK_DOMAIN）"
  else
    SLOWLINK_BIND_HOST=0.0.0.0
    log "网页模式：IP + 端口 HTTP"
  fi
  export SLOWLINK_WEB_MODE SLOWLINK_BIND_HOST SLOWLINK_WEB_PORT SLOWLINK_DOMAIN
}

cleanup_failed_install() {
  docker rm -f "$APP_CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$CADDY_CONTAINER" >/dev/null 2>&1 || true
}

rollback_previous_release() {
  warn "新版部署失败，正在恢复上一版本"
  save_web_access "$ORIGINAL_WEB_MODE" "$ORIGINAL_PORT" "$ORIGINAL_DOMAIN" "$ORIGINAL_BIND_HOST"
  SLOWLINK_WEB_MODE=$ORIGINAL_WEB_MODE
  SLOWLINK_BIND_HOST=$ORIGINAL_BIND_HOST
  SLOWLINK_WEB_PORT=$ORIGINAL_PORT
  SLOWLINK_DOMAIN=$ORIGINAL_DOMAIN
  export SLOWLINK_WEB_MODE SLOWLINK_BIND_HOST SLOWLINK_WEB_PORT SLOWLINK_DOMAIN
  restore_program_files "$PROGRAM_BACKUP"
  if (deploy_application update && install_watchdog && ensure_web_proxy && verify_installation); then
    warn "已恢复上一版本，Redis、Session 和用户配置均已保留"
    return 0
  fi
  warn "自动恢复上一版本失败，请查看上方中文诊断"
  return 1
}

perform_installation() {
  deploy_application "$1"
  install_watchdog
  ensure_web_proxy || die "网页代理配置失败"
  verify_installation || die "安装后完整验证失败"
}

apply_release_transaction() {
  transaction_mode=$1
  copy_release_files "$STAGE"
  save_web_access "$SLOWLINK_WEB_MODE" "$SLOWLINK_WEB_PORT" "$SLOWLINK_DOMAIN"
  perform_installation "$transaction_mode"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)
      [ "$#" -ge 2 ] || die "--version 缺少版本号"
      REQUESTED_VERSION=${2#v}
      shift 2
      ;;
    --port)
      [ "$#" -ge 2 ] || die "--port 缺少端口号"
      REQUESTED_PORT=$2
      shift 2
      ;;
    --domain)
      [ "$#" -ge 2 ] || die "--domain 缺少域名"
      REQUESTED_DOMAIN=$2
      shift 2
      ;;
    --http)
      FORCE_HTTP=1
      shift
      ;;
    --update)
      UPDATE_ONLY=1
      shift
      ;;
    --uninstall)
      require_root
      run_installed_uninstall preserve
      exit $?
      ;;
    --purge)
      require_root
      run_installed_uninstall purge
      exit $?
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *) die "未知参数：$1" ;;
  esac
done

if [ -n "$REQUESTED_DOMAIN" ] && [ "$FORCE_HTTP" -eq 1 ]; then
  die "--domain 与 --http 不能同时使用"
fi

require_root
if [ "$SHOW_MENU" -eq 1 ]; then
  main_menu
fi
if [ "$UPDATE_ONLY" -eq 1 ] && [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
  die "尚未检测到安装，请先执行安装"
fi

check_supported_os
TMP_DIR=$(mktemp -d /tmp/slowlink-install.XXXXXX)
install_dependencies
ensure_docker "$TMP_DIR"
if [ "$UPDATE_ONLY" -eq 0 ] && docker inspect "$APP_CONTAINER" >/dev/null 2>&1; then
  die "检测到 slowlink_app 已存在，请使用更新，避免无备份覆盖现有程序"
fi
ORIGINAL_WEB_MODE=$(read_web_mode)
ORIGINAL_BIND_HOST=$(read_web_bind_host)
ORIGINAL_PORT=$(read_web_port)
ORIGINAL_DOMAIN=$(read_web_domain)
select_web_access
download_release "$REQUESTED_VERSION" "$TMP_DIR"
validate_release_archive "$FULL_FILE"
STAGE="$TMP_DIR/stage"
extract_release_archive "$FULL_FILE" "$STAGE"
if [ "$SLOWLINK_WEB_MODE" = "https" ] && [ ! -f "$STAGE/ops/Caddyfile" ]; then
  die "所选 Release 不支持域名 HTTPS，请安装最新版本或改用 --http"
fi
PROGRAM_BACKUP="$TMP_DIR/program-backup"
if [ "$UPDATE_ONLY" -eq 1 ]; then
  backup_program_files "$PROGRAM_BACKUP"
fi

if [ "$UPDATE_ONLY" -eq 1 ]; then
  if ! (apply_release_transaction update); then
    KEEP_TMP_DIR=1
    if (rollback_previous_release); then
      KEEP_TMP_DIR=0
    else
      warn "自动恢复失败，旧程序备份保留在：$PROGRAM_BACKUP"
    fi
    exit 1
  fi
else
  if ! (apply_release_transaction install); then
    cleanup_failed_install
    exit 1
  fi
fi

installed_version=$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || printf '未知')
log "完成：SlowLink $installed_version，容器健康检查已通过"
log "网页地址：$(web_access_url)"
printf '管理命令：sudo %s/manage.sh status\n' "$INSTALL_DIR"
