import asyncio
import hashlib
import json
import secrets
import threading
import time

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

import telegram_login
from dedup import build_profile, clear_ttl_cache, list_dedup_recent, release_dedup, ttl_minutes_for_activity, ttl_minutes_for_profile
from bot_runner import manager
from config import WEB_HOST, WEB_PORT, APP_VERSION
from dialog_guard import should_keep_existing_dialog_cache
from matcher import match_rule_details, rule_diagnostics, invalidate_rule_cache
from code_rules import add_code_rule, code_rule_diagnostics, delete_code_rule, get_code_rules, reset_code_rules, save_code_rules, update_code_rule
from redis_store import (
    add_fail,
    cache_stats,
    clear_timezone_cache,
    delete,
    delete_pattern,
    ACTIVE_DEDUP_PATTERNS,
    DEDUP_META_PATTERNS,
    ensure_defaults,
    get,
    get_json,
    list_events,
    list_fails,
    list_hits,
    list_perf_events,
    log_line,
    push_event,
    r,
    sadd,
    scan_keys,
    set_json,
    set_value,
    smembers,
    format_time,
    srem,
    trim_runtime_lists,
    clear_stats_cache,
)

try:
    from redis_store import clear_collisions as _clear_collisions
    from redis_store import dedup_stats as _dedup_stats
    from redis_store import list_collisions as _list_collisions
    from redis_store import mark_collision_distinct as _mark_collision_distinct
except Exception:
    def _clear_collisions() -> None:
        pass

    def _dedup_stats() -> dict:
        return {}

    def _list_collisions(_limit: int = 30) -> list:
        return []

    def _mark_collision_distinct(_identity: str, _dedup_id: str) -> bool:
        return False

app = Flask(__name__)
app.secret_key = get("web_secret") or secrets.token_hex(32)
set_value("web_secret", app.secret_key)
ensure_defaults()
trim_runtime_lists()
log_line("info", f"SlowLink {APP_VERSION} 启动")
# Avoid stale status after container rebuild/restart, but preserve whether the user
# wanted the listener to stay running across container restarts.
set_value("bot_status", "starting")


def _restore_listener_after_startup() -> None:
    time.sleep(3)
    try:
        desired = get("listener_desired_state", "stopped") or "stopped"
        if desired != "running":
            set_value("bot_status", "stopped")
            return
        if manager.is_running():
            set_value("bot_status", "running")
            return
        api_id = int(get("tg_api_id", "0") or 0)
        api_hash = get("tg_api_hash", "") or ""
        if not api_id or not api_hash:
            set_value("bot_status", "stopped")
            push_event("warning", "容器启动后未自动恢复监听：Telegram API 未配置完整")
            return
        if (get("tg_logged_in", "0") or "0") != "1":
            set_value("bot_status", "stopped")
            push_event("warning", "容器启动后未自动恢复监听：Telegram 尚未登录")
            return
        manager.start()
    except Exception as e:
        set_value("bot_status", "error")
        add_fail({"stage": "restore_listener", "error": str(e)})


threading.Thread(target=_restore_listener_after_startup, daemon=True, name="slowlink-restore-listener").start()


@app.context_processor
def inject_global_template_vars():
    # Make login/init/logout pages use the same version source as the dashboard.
    # This prevents the header from showing only the blue version badge background
    # or an old hard-coded version after logout.
    return {"app_version": APP_VERSION}


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    """Make AJAX endpoints always return JSON instead of Flask/HTML error pages."""
    if isinstance(e, HTTPException):
        if wants_json():
            return jsonify({"ok": False, "message": e.description or e.name, "kind": "error"}), e.code
        return e
    try:
        add_fail({"stage": "web", "error": str(e)})
        push_event("error", f"网页接口异常：{e}")
    except Exception:
        pass
    if wants_json():
        return jsonify({"ok": False, "message": f"网页接口异常：{e}", "kind": "error"}), 500
    raise e


def run_async(coro):
    return asyncio.run(coro)


def wants_json() -> bool:
    return request.headers.get("X-SlowLink-Ajax") == "1" or "application/json" in request.headers.get("Accept", "")


def flash_msg(msg: str, kind: str = "info"):
    session["msg"] = msg
    session["msg_kind"] = kind


def done(message: str, kind: str = "success", ok: bool = True, **extra):
    if wants_json():
        payload = {"ok": bool(ok), "message": message, "kind": kind}
        payload.update(extra)
        return jsonify(payload)
    flash_msg(message, kind)
    return redirect(url_for("index"))


def password_hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def is_initialized() -> bool:
    return bool(get("admin_password_hash"))


def is_login() -> bool:
    return session.get("logged_in") is True


def require_login():
    if not is_initialized():
        if wants_json():
            return jsonify({"ok": False, "message": "后台还没有初始化，请刷新页面完成初始化", "kind": "error"}), 401
        return redirect(url_for("init"))
    if not is_login():
        if wants_json():
            return jsonify({"ok": False, "message": "登录状态已失效，请刷新页面重新登录后台", "kind": "error"}), 401
        return redirect(url_for("login"))
    return None


def _variants_for_dialog(d: dict) -> set[str]:
    variants = {str(d.get("value", "")).strip().lower()}
    username = str(d.get("username", "")).strip().lower()
    if username:
        variants.add(username)
        variants.add(username.lstrip("@"))
        variants.add("@" + username.lstrip("@"))
    did = str(d.get("id", "")).strip().lower()
    if did:
        variants.add(did)
        variants.add(did.lstrip("-"))
        if did.startswith("-100"):
            variants.add(did[4:])
        elif did.lstrip("-").isdigit():
            variants.add("-100" + did.lstrip("-"))
    return {x for x in variants if x}


def _prepare_dialog_cache():
    dialogs = get_json("dialog_cache", []) or []
    monitors = {str(x).strip().lower() for x in smembers("monitor_chats")}
    excludes = {str(x).strip().lower() for x in smembers("exclude_chats")}
    prepared = []
    for raw in dialogs:
        d = dict(raw)
        variants = _variants_for_dialog(d)
        checked = bool(variants & monitors)
        excluded = bool(variants & excludes)
        d["checked"] = checked
        d["excluded"] = excluded
        d["status"] = "excluded" if excluded else ("listening" if checked else "unlistened")
        d["status_text"] = "已排除" if excluded else ("正在监听" if checked else "未监听")
        prepared.append(d)
    return prepared


def _dialog_stats(dialogs=None) -> dict:
    dialogs = _prepare_dialog_cache() if dialogs is None else dialogs
    total = len(dialogs)
    monitor_saved = len(smembers("monitor_chats"))
    listening = sum(1 for d in dialogs if d.get("checked") and not d.get("excluded"))
    excluded = sum(1 for d in dialogs if d.get("excluded"))
    unlistened = max(total - listening - excluded, 0)
    return {
        "total": total,
        "listening": listening,
        "unlistened": unlistened,
        "excluded": excluded,
        "monitor_saved": monitor_saved,
    }




def _format_age(seconds: int | float | None) -> str:
    try:
        sec = max(0, int(float(seconds or 0)))
    except Exception:
        sec = 0
    if sec < 60:
        return f"{sec} 秒前"
    minute = sec // 60
    if minute < 60:
        return f"{minute} 分钟前"
    hour = minute // 60
    minute = minute % 60
    if hour < 24:
        return f"{hour} 小时 {minute} 分钟前"
    day = hour // 24
    hour = hour % 24
    return f"{day} 天 {hour} 小时前"


def _heartbeat_payload() -> dict:
    """Return listener heartbeat info for the monitor panel.

    The listener writes listener_heartbeat_ts about once every two minutes, while normal Docker logs
    are throttled. The page should still show the latest heartbeat so the user
    can tell whether the listener is alive without reading logs.
    """
    status = get("bot_status", "stopped") or "stopped"
    raw = get("listener_heartbeat_ts", "") or ""
    ts = 0
    try:
        ts = int(float(raw)) if str(raw).strip() else 0
    except Exception:
        ts = 0
    now = int(time.time())
    age = max(0, now - ts) if ts else None
    if status != "running":
        health = "未运行"
        health_level = "idle"
    elif not ts:
        health = "等待心跳"
        health_level = "warn"
    elif age <= 120:
        health = "正常"
        health_level = "ok"
    elif age <= 300:
        health = "可能卡住"
        health_level = "warn"
    else:
        health = "异常，建议重启监听"
        health_level = "bad"
    return {
        "ts": ts,
        "time": format_time(ts) if ts else "-",
        "age_seconds": age,
        "age_text": _format_age(age) if age is not None else "-",
        "health": health,
        "health_level": health_level,
    }


def _public_link_domain() -> str:
    value = str(get("public_link_domain", "t.me") or "t.me").strip().lower()
    return value if value in {"t.me", "telegram.me"} else "t.me"


def _daily_stats_safe() -> dict:
    try:
        from redis_store import daily_stats
        return daily_stats()
    except Exception:
        return {}


def _state_payload(light: bool = False) -> dict:
    if light:
        return {
            "app_version": APP_VERSION,
            "tg_logged_in": get("tg_logged_in", "0") == "1",
            "tg_current_user": get("tg_current_user", "未登录") or "未登录",
            "bot_status": get("bot_status", "stopped") or "stopped",
            "target_chat": get("target_chat", "") or "",
            "public_link_domain": _public_link_domain(),
            "heartbeat": _heartbeat_payload(),
        }
    dialogs = [] if light else _prepare_dialog_cache()
    stats = _dialog_stats(dialogs if dialogs else None)
    data = {
        "app_version": APP_VERSION,
        "tg_logged_in": get("tg_logged_in", "0") == "1",
        "tg_current_user": get("tg_current_user", "未登录") or "未登录",
        "bot_status": get("bot_status", "stopped") or "stopped",
        "target_chat": get("target_chat", "") or "",
        "public_link_domain": _public_link_domain(),
        "stats": stats,
        "monitor_chats": sorted(smembers("monitor_chats")),
        "exclude_chats": sorted(smembers("exclude_chats")),
        "exclude_texts": sorted(smembers("exclude_texts")),
        "regex_rules": sorted(smembers("regex_rules")),
        "code_rules": code_rule_diagnostics(),
        "events": list_events(30),
        "hits": list_hits(30),
        "fails": list_fails(30),
        "perf_events": list_perf_events(30),
        "dedup_recent": list_dedup_recent(30),
        "cache_stats": cache_stats(),
        "heartbeat": _heartbeat_payload(),
        "daily_stats": _daily_stats_safe(),
        "dedup_stats": _dedup_stats(),
        "collisions": _list_collisions(30),
    }
    if not light:
        data["dialog_cache"] = dialogs
    return data


def _page_data() -> dict:
    dialogs = _prepare_dialog_cache()
    return {
        "app_version": APP_VERSION,
        "tg_api_id": get("tg_api_id", "") or "",
        "tg_api_hash": get("tg_api_hash", "") or "",
        "tg_phone": get("tg_phone", "") or "",
        "tg_logged_in": get("tg_logged_in", "0") == "1",
        "tg_current_user": get("tg_current_user", "未登录") or "未登录",
        "bot_status": get("bot_status", "stopped") or "stopped",
        "target_chat": get("target_chat", "") or "",
        "public_link_domain": _public_link_domain(),
        "monitor_chats": sorted(smembers("monitor_chats")),
        "dialog_cache": dialogs,
        "dialog_stats": _dialog_stats(dialogs),
        "exclude_chats": sorted(smembers("exclude_chats")),
        "exclude_texts": sorted(smembers("exclude_texts")),
        "regex_rules": sorted(smembers("regex_rules")),
        "code_rules": code_rule_diagnostics(),
        "dedup_enabled": get("dedup_enabled", "1") == "1",
        "dedup_minutes": get("dedup_minutes", "20") or "20",
        "dedup_mode": get("dedup_mode", "strict") or "strict",
        "dedup_register_minutes": get("dedup_register_minutes", "20") or "20",
        "dedup_invite_minutes": get("dedup_invite_minutes", "0") or "0",
        "dedup_code_minutes": get("dedup_code_minutes", "20") or "20",
        "dedup_lottery_minutes": get("dedup_lottery_minutes", "720") or "720",
        "dedup_joint_lottery_minutes": get("dedup_joint_lottery_minutes", "4320") or "4320",
        "dedup_lottery_key_mode": get("dedup_lottery_key_mode", "id") or "id",
        "dedup_lottery_template_mode": get("dedup_lottery_template_mode", "global") or "global",
        "dedup_long_term_minutes": get("dedup_long_term_minutes", "10080") or "10080",
        "dedup_other_minutes": get("dedup_other_minutes", "20") or "20",
        "events": list_events(30),
        "hits": list_hits(30),
        "fails": list_fails(30),
        "perf_events": list_perf_events(30),
        "dedup_recent": list_dedup_recent(30),
        "regex_test_result": None,
        "precheck_result": None,
        "cache_stats": cache_stats(),
        "heartbeat": _heartbeat_payload(),
        "display_timezone": "Asia/Shanghai",
    }


@app.route("/init", methods=["GET", "POST"])
def init():
    if is_initialized():
        return redirect(url_for("index"))
    if request.method == "POST":
        p1 = request.form.get("password", "")
        p2 = request.form.get("password2", "")
        if len(p1) < 6:
            return render_template("init.html", error="密码至少 6 位")
        if p1 != p2:
            return render_template("init.html", error="两次密码不一致")
        salt = secrets.token_hex(16)
        set_value("admin_password_salt", salt)
        set_value("admin_password_hash", password_hash(p1, salt))
        push_event("success", "后台密码初始化完成")
        session["logged_in"] = True
        return redirect(url_for("index"))
    return render_template("init.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_initialized():
        return redirect(url_for("init"))
    if request.method == "POST":
        password = request.form.get("password", "")
        salt = get("admin_password_salt", "") or ""
        if password_hash(password, salt) == get("admin_password_hash"):
            session["logged_in"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="密码错误")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    gate = require_login()
    if gate:
        return gate
    data = _page_data()
    data["msg"] = session.pop("msg", "")
    data["msg_kind"] = session.pop("msg_kind", "info")
    return render_template("index.html", **data)


@app.route("/health")
def health():
    """Check the web process, Redis, listener intent, and message workers."""
    try:
        redis_ok = bool(r.ping())
    except Exception:
        redis_ok = False
    try:
        desired = get("listener_desired_state", "stopped") or "stopped"
        raw_heartbeat = get("listener_heartbeat_ts", "") or ""
    except Exception:
        desired = "unknown"
        raw_heartbeat = ""
    listener_running = manager.is_running()
    workers = manager.worker_status()
    workers_ok = workers["expected"] > 0 and workers["alive"] == workers["expected"]
    try:
        client_connected = bool(manager.client and manager.client.is_connected())
    except Exception:
        client_connected = False
    try:
        heartbeat_ts = int(float(raw_heartbeat)) if str(raw_heartbeat).strip() else 0
    except Exception:
        heartbeat_ts = 0
    heartbeat_age = max(0, int(time.time()) - heartbeat_ts) if heartbeat_ts else None
    heartbeat_fresh = heartbeat_age is not None and heartbeat_age <= 300
    listener_ready = (
        listener_running
        and manager.started_ok
        and workers_ok
        and client_connected
        and heartbeat_fresh
    )
    if desired == "running":
        listener_ok = listener_ready
    elif desired == "stopped":
        listener_ok = (
            not listener_running
            and not manager.started_ok
            and not client_connected
            and workers["alive"] == 0
            and workers["active_jobs"] == 0
        )
    else:
        listener_ok = False
    ok = redis_ok and listener_ok
    payload = {
        "status": "ok" if ok else "degraded",
        "version": APP_VERSION,
        "redis": redis_ok,
        "listener_desired_state": desired,
        "listener_running": listener_running,
        "listener_ready": listener_ready,
        "client_connected": client_connected,
        "heartbeat_age": heartbeat_age,
        "workers": workers,
    }
    return jsonify(payload), 200 if ok else 503


@app.get("/api/state")
def api_state():
    gate = require_login()
    if gate:
        return jsonify({"ok": False, "message": "未登录"}), 401
    light = request.args.get("light") == "1"
    return jsonify({"ok": True, **_state_payload(light=light)})


@app.post("/save_api")
def save_api():
    gate = require_login()
    if gate:
        return gate
    try:
        telegram_login.save_api(request.form.get("api_id"), request.form.get("api_hash"), request.form.get("phone"))
        return done("API 配置已保存", "success")
    except Exception as e:
        return done(f"操作失败：{e}", "error", ok=False)


@app.post("/send_code")
def send_code():
    gate = require_login()
    if gate:
        return gate
    try:
        msg = run_async(telegram_login.send_code())
        return done(msg, "success")
    except Exception as e:
        add_fail({"stage": "send_code", "error": str(e)})
        return done(f"发送验证码失败：{e}", "error", ok=False)


@app.post("/sign_in")
def sign_in():
    gate = require_login()
    if gate:
        return gate
    try:
        info = run_async(telegram_login.sign_in(request.form.get("code", ""), request.form.get("password", "")))
        return done(f"登录成功：{info.get('username') or info.get('first_name') or info.get('id')}", "success")
    except Exception as e:
        add_fail({"stage": "sign_in", "error": str(e)})
        return done(f"登录失败：{e}", "error", ok=False)


@app.post("/tg_logout")
def tg_logout():
    gate = require_login()
    if gate:
        return gate
    try:
        set_value("listener_desired_state", "stopped")
        manager.stop()
        msg = run_async(telegram_login.logout())
        return done(msg, "success")
    except Exception as e:
        add_fail({"stage": "tg_logout", "error": str(e)})
        return done(f"退出失败：{e}", "error", ok=False)


@app.post("/start_bot")
def start_bot():
    gate = require_login()
    if gate:
        return gate
    try:
        set_value("listener_desired_state", "running")
        return done(manager.start(), "success")
    except Exception as e:
        add_fail({"stage": "start_bot", "error": str(e)})
        return done(f"启动失败：{e}", "error", ok=False)


@app.post("/stop_bot")
def stop_bot():
    gate = require_login()
    if gate:
        return gate
    try:
        set_value("listener_desired_state", "stopped")
        return done(manager.stop(), "success")
    except Exception as e:
        add_fail({"stage": "stop_bot", "error": str(e)})
        return done(f"停止失败：{e}", "error", ok=False)


@app.post("/set_target")
def set_target():
    gate = require_login()
    if gate:
        return gate
    value = request.form.get("value", "").strip()
    if value:
        set_value("target_chat", value)
        try:
            manager.clear_runtime_cache()
        except Exception:
            pass
        return done("转发目标已保存", "success")
    return done("目标不能为空", "error", ok=False)


@app.post("/set_link_domain")
def set_link_domain():
    gate = require_login()
    if gate:
        return gate
    value = request.form.get("value", "t.me").strip().lower()
    if value not in {"t.me", "telegram.me"}:
        return done("公开链接格式不正确", "error", ok=False)
    set_value("public_link_domain", value)
    try:
        manager.clear_runtime_cache()
    except Exception:
        pass
    label = "t.me 标准模式" if value == "t.me" else "telegram.me 兼容模式"
    return done(f"公开链接已切换为 {label}", "success")


@app.post("/test_send")
def test_send():
    gate = require_login()
    if gate:
        return gate
    try:
        msg = run_async(manager.test_send())
        return done(msg, "success")
    except Exception as e:
        add_fail({"stage": "test_send", "error": str(e)})
        return done(f"测试发送失败：{e}", "error", ok=False)


def _add_set(redis_key: str, form_key: str, ok_msg: str):
    value = request.form.get(form_key, "").strip()
    if value:
        sadd(redis_key, value)
        invalidate_rule_cache()
        try:
            manager.clear_runtime_cache()
        except Exception:
            pass
        return done(ok_msg, "success")
    return done("内容不能为空", "error", ok=False)


def _del_set(redis_key: str):
    value = request.form.get("value", "").strip()
    if value:
        srem(redis_key, value)
        invalidate_rule_cache()
        try:
            manager.clear_runtime_cache()
        except Exception:
            pass
        return done("已删除", "success")
    return done("删除失败：内容为空", "error", ok=False)


@app.post("/add_monitor")
def add_monitor():
    gate = require_login()
    if gate:
        return gate
    return _add_set("monitor_chats", "value", "监听会话已添加")


@app.post("/del_monitor")
def del_monitor():
    gate = require_login()
    if gate:
        return gate
    return _del_set("monitor_chats")


@app.post("/test_source")
def test_source():
    gate = require_login()
    if gate:
        return gate
    try:
        msg = run_async(manager.test_source(request.form.get("value", "")))
        return done(msg, "success")
    except Exception as e:
        add_fail({"stage": "test_source", "error": str(e)})
        return done(f"测试监听源失败：{e}", "error", ok=False)


@app.post("/refresh_dialogs")
def refresh_dialogs():
    gate = require_login()
    if gate:
        return gate
    try:
        old_dialogs = get_json("dialog_cache", []) or []
        dialogs = run_async(manager.list_dialogs(force=True))
        if not dialogs:
            add_fail({"stage": "refresh_dialogs", "error": "get_dialogs 返回 0 个群/频道，已保留旧缓存，不再覆盖为空"})
            return done("刷新失败：没有拉到任何群/频道。旧列表已保留，请看失败记录或 docker logs。", "warning", ok=False)
        old_count = len(old_dialogs)
        new_count = len(dialogs)
        if should_keep_existing_dialog_cache(old_count=old_count, new_count=new_count):
            message = f"刷新结果疑似不完整：旧列表 {old_count} 个，本次只拉到 {new_count} 个。已保留旧列表，请稍后再试。"
            add_fail({"stage": "refresh_dialogs", "error": message}, emit_log=False)
            push_event("warning", message)
            return done(message, "warning", ok=False)
        set_json("dialog_cache", dialogs)
        if not smembers("monitor_chats"):
            for d in dialogs:
                value = str(d.get("value", "")).strip()
                if value:
                    sadd("monitor_chats", value)
            msg = f"已刷新 {len(dialogs)} 个群/频道；当前监听为空，已默认全选监听。"
        else:
            msg = f"已刷新 {len(dialogs)} 个群/频道。原监听选择已保留。"
        try:
            manager.clear_runtime_cache()
        except Exception:
            pass
        push_event("success", msg)
        return done(msg, "success")
    except Exception as e:
        add_fail({"stage": "refresh_dialogs", "error": str(e)})
        return done(f"刷新监听面板失败：{e}", "error", ok=False)


@app.post("/save_monitor_panel")
def save_monitor_panel():
    gate = require_login()
    if gate:
        return gate
    selected = [x.strip() for x in request.form.getlist("monitors") if x.strip()]
    try:
        delete("monitor_chats")
        for value in selected:
            sadd("monitor_chats", value)
        try:
            manager.clear_runtime_cache()
        except Exception:
            pass
        return done(f"监听面板已保存：{len(selected)} 个群/频道", "success")
    except Exception as e:
        add_fail({"stage": "save_monitor_panel", "error": str(e)})
        return done(f"保存监听面板失败：{e}", "error", ok=False)


@app.post("/add_exclude")
def add_exclude():
    gate = require_login()
    if gate:
        return gate
    return _add_set("exclude_chats", "value", "排除会话已添加")


@app.post("/del_exclude")
def del_exclude():
    gate = require_login()
    if gate:
        return gate
    return _del_set("exclude_chats")


@app.post("/add_exclude_text")
def add_exclude_text():
    gate = require_login()
    if gate:
        return gate
    return _add_set("exclude_texts", "value", "排除文本已添加")


@app.post("/del_exclude_text")
def del_exclude_text():
    gate = require_login()
    if gate:
        return gate
    return _del_set("exclude_texts")


@app.post("/add_regex")
def add_regex():
    gate = require_login()
    if gate:
        return gate
    return _add_set("regex_rules", "value", "正则规则已添加")


@app.post("/del_regex")
def del_regex():
    gate = require_login()
    if gate:
        return gate
    return _del_set("regex_rules")


@app.post("/add_code_rule")
def add_code_rule_route():
    gate = require_login()
    if gate:
        return gate
    name = request.form.get("name", "").strip() or "自定义码规则"
    pattern = request.form.get("pattern", "").strip()
    group = request.form.get("group", "0").strip() or "0"
    fast = request.form.get("fast") == "1"
    trigger = request.form.get("trigger") == "1"
    strict_context = request.form.get("strict_context") != "0"
    if not pattern:
        return done("码识别正则不能为空", "error", ok=False)
    try:
        add_code_rule(name, pattern, group, fast, trigger, strict_context)
        return done("码识别规则已添加。默认仅辅助去重，不会单独触发转发。", "success")
    except Exception as e:
        return done(f"添加失败：{e}", "error", ok=False)


@app.post("/update_code_rule")
def update_code_rule_route():
    gate = require_login()
    if gate:
        return gate
    try:
        idx = int(request.form.get("index", "-1"))
        patch = {
            "name": request.form.get("name", "").strip() or "未命名码规则",
            "pattern": request.form.get("pattern", "").strip(),
            "group": request.form.get("group", "0").strip() or "0",
            "enabled": request.form.get("enabled") == "1",
            "fast": request.form.get("fast") == "1",
            "trigger": request.form.get("trigger") == "1",
            "strict_context": request.form.get("strict_context") == "1",
            "note": request.form.get("note", "").strip(),
        }
        if not patch["pattern"]:
            return done("码识别正则不能为空", "error", ok=False)
        if update_code_rule(idx, patch):
            return done("码识别规则已保存", "success")
        return done("保存失败：规则不存在", "error", ok=False)
    except Exception as e:
        return done(f"保存码识别规则失败：{e}", "error", ok=False)


@app.post("/del_code_rule")
def del_code_rule_route():
    gate = require_login()
    if gate:
        return gate
    try:
        idx = int(request.form.get("index", "-1"))
    except Exception:
        idx = -1
    if delete_code_rule(idx):
        return done("码识别规则已删除", "success")
    return done("删除失败：规则不存在", "error", ok=False)


@app.post("/reset_code_rules")
def reset_code_rules_route():
    gate = require_login()
    if gate:
        return gate
    reset_code_rules()
    return done("码识别规则已恢复默认", "success")


@app.post("/save_dedup")
def save_dedup():
    gate = require_login()
    if gate:
        return gate
    set_value("dedup_enabled", "1" if request.form.get("enabled") == "1" else "0")
    mode = request.form.get("mode", "strict")
    if mode not in {"strict", "balanced", "loose"}:
        mode = "strict"
    set_value("dedup_mode", mode)
    lottery_key_mode = request.form.get("dedup_lottery_key_mode", "id")
    if lottery_key_mode not in {"id", "id_keyword", "id_prize_keyword"}:
        lottery_key_mode = "id"
    set_value("dedup_lottery_key_mode", lottery_key_mode)
    lottery_template_mode = request.form.get("dedup_lottery_template_mode", "global")
    if lottery_template_mode not in {"global", "id", "off"}:
        lottery_template_mode = "global"
    set_value("dedup_lottery_template_mode", lottery_template_mode)
    allowed = {"0", "5", "10", "15", "20", "30", "60", "180", "360", "720", "1440", "4320", "10080", "20160"}
    fields = {
        "dedup_register_minutes": "20",
        "dedup_invite_minutes": "0",
        "dedup_code_minutes": "20",
        "dedup_lottery_minutes": "720",
        "dedup_joint_lottery_minutes": "4320",
        "dedup_long_term_minutes": "10080",
        "dedup_other_minutes": "20",
    }
    for key, default in fields.items():
        value = request.form.get(key, default)
        if value not in allowed:
            value = default
        set_value(key, value)
    set_value("dedup_minutes", request.form.get("dedup_other_minutes", "20") if request.form.get("dedup_other_minutes", "20") in allowed else "20")
    clear_ttl_cache()
    try:
        manager.clear_runtime_cache()
    except Exception:
        pass
    return done("去重策略已保存", "success")


@app.post("/release_dedup")
def release_dedup_route():
    gate = require_login()
    if gate:
        return gate
    did = request.form.get("dedup_id", "").strip()
    if release_dedup(did):
        push_event("warning", f"已手动解除去重：{did}")
        return done("已解除该条去重", "success")
    return done("解除失败：dedup_id 为空", "error", ok=False)


@app.post("/regex_test")
def regex_test():
    gate = require_login()
    if gate:
        return gate
    text = request.form.get("text", "")
    try:
        details = match_rule_details(text)
        profile = build_profile(text, "")
        ttl = ttl_minutes_for_profile(profile, None)
        diagnostics = rule_diagnostics()
        invalid = [x for x in diagnostics if not x.get("ok")]
        result = {
            "matched": details.get("matched"),
            "rule": details.get("rule"),
            "candidate": details.get("candidate"),
            "usage_notice": details.get("usage_notice"),
            "closed_register_notice": details.get("closed_register_notice"),
            "registration_success_notice": details.get("registration_success_notice"),
            "excluded_text_notice": details.get("excluded_text_notice"),
            "excluded_keyword": details.get("excluded_keyword", ""),
            "code_detected": details.get("code_detected"),
            "code_rule": details.get("code_rule", ""),
            "code_note": details.get("code_note", ""),
            "original": details.get("original", "")[:1200],
            "normalized": details.get("normalized", "")[:1200],
            "compact": details.get("compact", "")[:1200],
            "activity": profile.get("activity"),
            "core": profile.get("core", "")[:800],
            "code_identity": profile.get("code_identity", ""),
            "weak_code_identity": profile.get("weak_code_identity", ""),
            "content_url_identity": profile.get("content_url_identity", ""),
            "dedup_strategy": profile.get("dedup_strategy", ""),
            "ttl_policy": profile.get("ttl_policy", ""),
            "lottery_identity": profile.get("lottery_identity", ""),
            "lottery_mode": profile.get("lottery_mode", ""),
            "dedup_id": profile.get("dedup_id", ""),
            "ttl_minutes": ttl,
            "rule_count": len(diagnostics),
            "invalid_rules": invalid[:20],
        }
        if wants_json():
            return jsonify({"ok": True, "message": "正则测试完成" if result["matched"] else "正则测试完成：未命中", "kind": "success" if result["matched"] else "warning", "regex_test_result": result})
        session["regex_test_result"] = json.dumps(result, ensure_ascii=False)
        return done("正则测试完成", "success" if result["matched"] else "warning")
    except Exception as e:
        add_fail({"stage": "regex_test", "error": str(e)})
        return done(f"正则测试失败：{e}", "error", ok=False)


@app.post("/precheck")
def precheck():
    gate = require_login()
    if gate:
        return gate
    try:
        checks = []
        def add(name, ok, detail):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
        add("Telegram 登录", get("tg_logged_in", "0") == "1", get("tg_current_user", "未登录") or "未登录")
        target = get("target_chat", "") or ""
        add("转发目标", bool(target.strip()), target or "未设置")
        monitors = smembers("monitor_chats")
        add("监听列表", bool(monitors), f"{len(monitors)} 个")
        rules = smembers("regex_rules")
        add("正则规则", bool(rules), f"{len(rules)} 条原始规则")
        excluded_texts = smembers("exclude_texts")
        add("排除文本", True, f"{len(excluded_texts)} 条")
        diagnostics = rule_diagnostics()
        invalid = [x for x in diagnostics if not x.get("ok")]
        add("正则编译", not invalid, "全部正常" if not invalid else f"{len(invalid)} 条异常")
        code_diag = code_rule_diagnostics()
        bad_code_rules = [x for x in code_diag if not x.get("ok")]
        add("码识别规则", not bad_code_rules, f"{len(code_diag)} 条" if not bad_code_rules else f"{len(bad_code_rules)} 条异常")
        add("Redis", True, "正常")
        result = {"ok": all(x["ok"] for x in checks), "checks": checks, "invalid_rules": invalid[:20]}
        if wants_json():
            return jsonify({"ok": True, "message": "启动自检完成", "kind": "success" if result["ok"] else "warning", "precheck_result": result})
        session["precheck_result"] = json.dumps(result, ensure_ascii=False)
        return done("启动自检完成", "success" if result["ok"] else "warning")
    except Exception as e:
        add_fail({"stage": "precheck", "error": str(e)})
        return done(f"启动自检失败：{e}", "error", ok=False)




@app.post("/save_display")
def save_display():
    gate = require_login()
    if gate:
        return gate
    tz = request.form.get("display_timezone", "Asia/Shanghai").strip() or "Asia/Shanghai"
    allowed = {"Asia/Shanghai", "UTC"}
    if tz not in allowed:
        tz = "Asia/Shanghai"
    set_value("display_timezone", tz)
    clear_timezone_cache()
    return done("显示时间设置已保存", "success")


@app.get("/api/cache_stats")
def api_cache_stats():
    gate = require_login()
    if gate:
        return jsonify({"ok": False, "message": "未登录"}), 401
    return jsonify({"ok": True, "cache_stats": cache_stats()})


@app.get("/api/health_summary")
def api_health_summary():
    gate = require_login()
    if gate:
        return jsonify({"ok": False, "message": "未登录"}), 401
    return jsonify({
        "ok": True,
        "daily_stats": _daily_stats_safe(),
        "flow_stats": get_json("listener_flow_stats", {}) or {},
        "cache_stats": cache_stats(),
    })


@app.get("/api/dedup_stats")
def api_dedup_stats():
    gate = require_login()
    if gate:
        return jsonify({"ok": False, "message": "未登录"}), 401
    return jsonify({"ok": True, "dedup_stats": _dedup_stats(), "collisions": _list_collisions(30)})


@app.post("/clear_collisions")
def clear_collisions_route():
    gate = require_login()
    if gate:
        return gate
    _clear_collisions()
    return done("抽奖碰撞记录已清空", "success")


@app.post("/mark_collision_distinct")
def mark_collision_distinct_route():
    gate = require_login()
    if gate:
        return gate
    identity = request.form.get("identity", "").strip()
    dedup_id = request.form.get("dedup_id", "").strip()
    if not identity or not dedup_id:
        return done("缺少碰撞身份或去重 ID", "error", ok=False)
    if _mark_collision_distinct(identity, dedup_id):
        push_event("warning", f"已标记为不同抽奖：{identity[:120]}")
        return done("已标记，该文本后续不再被此抽奖身份拦截", "success")
    return done("标记失败", "error", ok=False)


@app.get("/export_config")
def export_config():
    gate = require_login()
    if gate:
        return gate
    payload = {
        "version": APP_VERSION,
        "export_time": format_time(),
        "target_chat": get("target_chat", "") or "",
        "public_link_domain": _public_link_domain(),
        "monitor_chats": sorted(smembers("monitor_chats")),
        "exclude_chats": sorted(smembers("exclude_chats")),
        "exclude_texts": sorted(smembers("exclude_texts")),
        "regex_rules": sorted(smembers("regex_rules")),
        "code_rules": get_code_rules(),
        "dedup": {
            "enabled": get("dedup_enabled", "1") or "1",
            "mode": get("dedup_mode", "strict") or "strict",
            "register_minutes": get("dedup_register_minutes", "20") or "20",
            "invite_minutes": get("dedup_invite_minutes", "0") or "0",
            "code_minutes": get("dedup_code_minutes", "20") or "20",
            "lottery_minutes": get("dedup_lottery_minutes", "720") or "720",
            "joint_lottery_minutes": get("dedup_joint_lottery_minutes", "4320") or "4320",
            "lottery_key_mode": get("dedup_lottery_key_mode", "id") or "id",
            "lottery_template_mode": get("dedup_lottery_template_mode", "global") or "global",
            "long_term_minutes": get("dedup_long_term_minutes", "10080") or "10080",
            "other_minutes": get("dedup_other_minutes", "20") or "20",
        },
        "ui": {
            "display_timezone": get("display_timezone", "Asia/Shanghai") or "Asia/Shanghai",
        },
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    filename = "slowlink_backup_" + time.strftime("%Y%m%d_%H%M%S") + ".json"
    return Response(data, mimetype="application/json", headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.post("/import_config")
def import_config():
    gate = require_login()
    if gate:
        return gate
    try:
        mode = request.form.get("mode", "overwrite")
        if mode not in {"overwrite", "merge", "rules_only"}:
            mode = "overwrite"
        upload = request.files.get("backup_file")
        raw = ""
        if upload and upload.filename:
            raw = upload.read().decode("utf-8", errors="ignore").strip()
        else:
            raw = request.form.get("config_json", "").strip()
        if not raw:
            raise ValueError("没有选择备份文件")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("备份文件格式不正确")
        imported = []
        matcher_rules_changed = False
        if mode != "rules_only" and "target_chat" in payload:
            set_value("target_chat", str(payload.get("target_chat") or ""))
            imported.append("转发目标")
        if mode != "rules_only" and "public_link_domain" in payload:
            domain = str(payload.get("public_link_domain") or "").strip().lower()
            if domain in {"t.me", "telegram.me"}:
                set_value("public_link_domain", domain)
                imported.append("公开链接格式")
        set_items = [
            ("monitor_chats", "monitor_chats", "监听列表"),
            ("exclude_chats", "exclude_chats", "排除列表"),
            ("exclude_texts", "exclude_texts", "排除文本"),
            ("regex_rules", "regex_rules", "正则规则"),
        ]
        for redis_key, json_key, label in set_items:
            items = payload.get(json_key)
            if isinstance(items, list):
                if mode in {"overwrite", "rules_only"}:
                    delete(redis_key)
                for item in items:
                    sadd(redis_key, str(item))
                imported.append(label)
                if redis_key in {"exclude_texts", "regex_rules"}:
                    matcher_rules_changed = True
        if mode != "rules_only" and isinstance(payload.get("code_rules"), list):
            save_code_rules(payload.get("code_rules") or [])
            imported.append("码识别规则")
        if matcher_rules_changed:
            invalidate_rule_cache()
        if mode != "rules_only":
            d = payload.get("dedup") or {}
            mapping = {
                "enabled": "dedup_enabled",
                "mode": "dedup_mode",
                "register_minutes": "dedup_register_minutes",
                "invite_minutes": "dedup_invite_minutes",
                "code_minutes": "dedup_code_minutes",
                "lottery_minutes": "dedup_lottery_minutes",
                "joint_lottery_minutes": "dedup_joint_lottery_minutes",
                "lottery_key_mode": "dedup_lottery_key_mode",
                "lottery_template_mode": "dedup_lottery_template_mode",
                "long_term_minutes": "dedup_long_term_minutes",
                "other_minutes": "dedup_other_minutes",
            }
            changed_dedup = False
            for src, dst in mapping.items():
                if src in d:
                    set_value(dst, d[src])
                    changed_dedup = True
            if changed_dedup:
                imported.append("去重设置")
            ui = payload.get("ui") or {}
            if isinstance(ui, dict) and ui.get("display_timezone"):
                set_value("display_timezone", str(ui.get("display_timezone") or "Asia/Shanghai"))
                clear_timezone_cache()
                imported.append("显示设置")
        try:
            manager.clear_runtime_cache()
        except Exception:
            pass
        return done("配置已导入：" + ("、".join(imported) if imported else "无可导入内容"), "success")
    except Exception as e:
        add_fail({"stage": "import_config", "error": str(e)})
        return done(f"导入配置失败：{e}", "error", ok=False)


@app.post("/clear_logs")
def clear_logs():
    gate = require_login()
    if gate:
        return gate
    clear_stats_cache()
    kind = request.form.get("kind", "all")
    if kind == "events":
        delete("events")
        return done("系统事件已清空", "success", cache_stats=cache_stats())
    if kind == "hits":
        delete("hits")
        return done("命中记录已清空", "success", cache_stats=cache_stats())
    if kind == "fails":
        delete("fails")
        return done("失败 / 慢转发记录已清空", "success", cache_stats=cache_stats())
    if kind == "perf_events":
        delete("perf_events")
        return done("性能诊断记录已清空", "success", cache_stats=cache_stats())
    if kind == "dedup_recent":
        delete("dedup:recent")
        return done("去重显示记录已清空，不影响防重复缓存", "success", cache_stats=cache_stats())
    delete("events", "hits", "fails", "dedup:recent", "perf_events")
    return done("全部显示记录已清空", "success", cache_stats=cache_stats())


@app.post("/clear_cache")
def clear_cache():
    gate = require_login()
    if gate:
        return gate
    clear_stats_cache()
    kind = request.form.get("kind", "records")
    try:
        deleted = 0
        if kind == "records":
            delete("events", "hits", "fails", "dedup:recent", "perf_events")
            msg = "页面显示记录已清空，不影响转发和防重复"
        elif kind == "dedup_records":
            delete("dedup:recent")
            msg = "去重显示记录已清空，不影响防重复缓存"
        elif kind == "dialogs":
            delete("dialog_cache")
            try:
                manager.clear_runtime_cache()
            except Exception:
                pass
            msg = "会话缓存已清空；需要时请重新刷新全部群/频道"
        elif kind == "runtime":
            for pattern in ["tmp:*", "temp:*", "test:*", "runtime:*"]:
                deleted += delete_pattern(pattern)
            try:
                manager.clear_runtime_cache()
            except Exception:
                pass
            msg = f"临时运行缓存已清空；删除 {deleted} 个 key"
        elif kind == "dedup_cache":
            confirm_code = (request.form.get("confirm_code") or "").strip()
            if confirm_code != "CLEAR":
                return done("没有清空防重复缓存：请在确认框输入 CLEAR。", "warning", ok=False, cache_stats=cache_stats())
            before = cache_stats().get("dedup_cache", 0)
            for pattern in ACTIVE_DEDUP_PATTERNS + DEDUP_META_PATTERNS:
                deleted += delete_pattern(pattern)
            delete("dedup:records", "dedup:recent")
            clear_stats_cache()
            after = cache_stats().get("dedup_cache", 0)
            msg = f"防重复缓存已清空：{before} → {after}；相关去重显示记录也已清理。旧内容再次出现时可能重新转发。"
        elif kind in {"dedup_all", "all_safe"}:
            return done("已取消这个入口。需要清防重复缓存，请展开高级操作并输入 CLEAR。", "warning", ok=False, cache_stats=cache_stats())
        else:
            return done("未知缓存类型", "error", ok=False, cache_stats=cache_stats())
        push_event("warning", msg)
        return done(msg, "success", cache_stats=cache_stats())
    except Exception as e:
        add_fail({"stage": "clear_cache", "error": str(e)})
        return done(f"清理缓存失败：{e}", "error", ok=False, cache_stats=cache_stats())
