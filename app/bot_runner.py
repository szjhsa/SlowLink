import asyncio
import threading
import time
import random
from datetime import datetime, timezone
from typing import Optional

from telethon import TelegramClient, events, utils as telethon_utils
from telethon.errors import FloodWaitError

from config import SESSION_PATH, LISTENER_WORKERS, LOG_VERBOSE
from dedup import check_and_mark, release_dedup
from link_builder import (
    build_entity_cache_from_index,
    build_entity_index,
    build_message_link,
    get_chat_keys,
    normalize_chat_value,
    resolve_entity,
    split_dialog_values,
    build_entity_cache,
)
from matcher import analyze_message, get_text
from code_rules import extract_code_detail
from redis_store import add_fail, add_hit, add_perf_event, format_time, get, get_json, log_line, push_event, r, set_json, set_value, sha, smembers
import json as _json
from telegram_session_lock import SESSION_LOCK


TELEGRAM_DELAY_HIGH_SECONDS = 30
TELEGRAM_DELAY_IMMEDIATE_RECONNECT_SECONDS = 60
TELEGRAM_DELAY_RECONNECT_COUNT = 2
TELEGRAM_DELAY_RECONNECT_COOLDOWN_SECONDS = 180
PRIORITY_WORKER_COUNT = 1
MIN_NORMAL_WORKERS = 1
MAX_NORMAL_WORKERS = 3
WORKER_EXPAND_NORMAL_QUEUE = 8
WORKER_EXPAND_PRIORITY_QUEUE = 4
WORKER_SHRINK_IDLE_SECONDS = 45.0


class BotManager:
    def __init__(self):
        self.thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client: Optional[TelegramClient] = None
        self.stop_event: Optional[asyncio.Event] = None
        self.lock = threading.RLock()
        self.started_ok = False
        self.entity_cache = {}
        self.queue: Optional[asyncio.Queue] = None
        self.workers = []
        self._worker_specs = []
        self._desired_normal_workers = MIN_NORMAL_WORKERS
        self._base_normal_workers = MIN_NORMAL_WORKERS
        self._worker_idle_since = None
        self._active_jobs = 0
        self._reconnect_in_progress = False
        self._set_cache = {}
        self._target_entity_cache = {}
        self._source_cache = {}
        self._str_cache = {}
        self._last_message_time = 0.0
        self._last_trim_ts = 0.0
        self._last_heartbeat_log = 0.0
        self._entity_cache_refreshed_ts = 0.0
        self._last_force_entity_refresh_ts = 0.0
        # Telegram push-delay self-healing state. Reconnect is deliberately
        # bounded by a cooldown so delayed bursts do not cause reconnect churn.
        self._telegram_delay_high_count = 0
        self._last_delay_reconnect_ts = time.time()  # starts from listener init, not epoch 0
        self._reconnect_requested = False
        self._reconnect_reason = ""
        self._dedup_settings = (0.0, True, "strict", 20, 20)  # (ts, enabled, mode, other_minutes, code_minutes)
        self._monitor_peer_ids = frozenset()
        self._monitor_filter_complete = False
        self._monitor_filter_dirty = True
        self._monitor_filter_refresh_ts = 0.0
        self._flow_last_publish_ts = time.monotonic()
        self._flow_counters = {
            "received": 0,
            "fast_filtered": 0,
            "handler_entered": 0,
            "fallback_filtered": 0,
            "enqueued": 0,
            "queue_full": 0,
            "matched": 0,
            "forwarded": 0,
        }

    def _cached_set(self, key: str, ttl: float = 30.0) -> set[str]:
        now = time.time()
        cached = self._set_cache.get(key)
        if cached and now - cached[0] <= ttl:
            return cached[1]
        value = {normalize_chat_value(x) for x in smembers(key)}
        self._set_cache[key] = (now, value)
        return value

    def _cached_str(self, key: str, ttl: float = 30.0) -> str:
        now = time.time()
        cached = self._str_cache.get(key)
        if cached and now - cached[0] <= ttl:
            return cached[1]
        value = get(key, "") or ""
        self._str_cache[key] = (now, value)
        return value

    def _cached_dedup_settings(self, ttl: float = 30.0) -> tuple[bool, str, int, int]:
        now = time.time()
        ts, enabled, mode, other_minutes, code_minutes = self._dedup_settings
        if now - ts <= ttl:
            return enabled, mode, other_minutes, code_minutes
        pipe = r.pipeline()
        pipe.get("dedup_enabled")
        pipe.get("dedup_mode")
        pipe.get("dedup_other_minutes")
        pipe.get("dedup_code_minutes")
        raw_enabled, raw_mode, raw_other, raw_code = pipe.execute()
        enabled = (raw_enabled or "1") == "1"
        mode = raw_mode if raw_mode in {"strict", "balanced", "loose"} else "strict"
        try: other_minutes = int(raw_other or 20)
        except Exception: other_minutes = 20
        try: code_minutes = int(raw_code or 20)
        except Exception: code_minutes = 20
        self._dedup_settings = (now, enabled, mode, other_minutes, code_minutes)
        return enabled, mode, other_minutes, code_minutes

    def clear_runtime_cache(self):
        self._set_cache.clear()
        self._target_entity_cache.clear()
        self._source_cache.clear()
        self._str_cache.clear()
        self._monitor_filter_dirty = True

    async def _refresh_entity_cache(self, client: TelegramClient, force: bool = False):
        """Load the persisted entity index first, then fall back to get_dialogs."""
        if not force:
            try:
                index = get_json("entity_index", {}) or {}
                cached = build_entity_cache_from_index(index)
                if cached:
                    self.entity_cache = cached
                    self._entity_cache_refreshed_ts = time.monotonic()
                    return None
            except Exception:
                pass
        self._last_force_entity_refresh_ts = time.monotonic()
        dialogs = await client.get_dialogs(limit=None)
        self.entity_cache = build_entity_cache(dialogs)
        try:
            set_json("entity_index", build_entity_index(dialogs))
        except Exception:
            pass
        self._entity_cache_refreshed_ts = time.monotonic()
        return dialogs

    def _refresh_monitor_peer_filter(self):
        monitors = self._cached_set("monitor_chats", ttl=0.0)
        peer_ids: set[int] = set()
        unresolved = []
        for value in monitors:
            key = normalize_chat_value(value)
            entity = self.entity_cache.get(key)
            if entity is None and key.startswith("@"):
                entity = self.entity_cache.get(key[1:])
            elif entity is None and key and not key.startswith("@"):
                entity = self.entity_cache.get("@" + key)
            if entity is not None:
                try:
                    peer_ids.add(int(telethon_utils.get_peer_id(entity)))
                    continue
                except Exception:
                    pass
            if key.startswith("-") and key[1:].isdigit():
                peer_ids.add(int(key))
                continue
            unresolved.append(key)
        self._monitor_peer_ids = frozenset(peer_ids)
        self._monitor_filter_complete = not unresolved
        self._monitor_filter_dirty = False
        self._monitor_filter_refresh_ts = time.monotonic()

    def _fast_event_filter(self, event):
        self._flow_counters["received"] += 1
        if self._monitor_filter_dirty or not self._monitor_filter_complete or not self._monitor_peer_ids:
            return True
        try:
            chat_id = getattr(event, "chat_id", None)
            if chat_id is not None:
                chat_id = int(chat_id)
            if chat_id is not None and chat_id not in self._monitor_peer_ids:
                self._flow_counters["fast_filtered"] += 1
                return False
        except Exception:
            return True
        return True

    def _publish_flow_stats(self, now: float | None = None, force: bool = False):
        now = float(now if now is not None else time.monotonic())
        window_seconds = max(1, int(now - self._flow_last_publish_ts))
        if not force and window_seconds < 60:
            return
        stats = dict(self._flow_counters)
        stats.update({
            "ts": int(time.time()),
            "window_seconds": window_seconds,
            "normal_queue": self.queue.qsize() if self.queue else 0,
            "priority_queue": self.priority_queue.qsize() if self.priority_queue else 0,
            "monitor_peer_ids": len(self._monitor_peer_ids),
            "monitor_filter_complete": self._monitor_filter_complete,
        })
        try:
            r.set("listener_flow_stats", _json.dumps(stats, ensure_ascii=False))
        except Exception:
            return
        for key in self._flow_counters:
            self._flow_counters[key] = 0
        self._flow_last_publish_ts = now

    def _perf_int(self, value) -> int:
        try:
            return int(float(value or 0))
        except Exception:
            return 0

    def _is_slow_perf(self, perf: dict) -> bool:
        return (
            self._perf_int(perf.get("total_ms")) >= 1000
            or self._perf_int(perf.get("queue_wait_ms")) >= 1000
            or self._perf_int(perf.get("match_ms")) >= 300
            or self._perf_int(perf.get("before_send_ms")) >= 1000
        )

    def _record_perf_event(self, source_name: str, rule: str, link: str, result: str, perf: dict, extra: dict | None = None) -> None:
        try:
            add_perf_event({
                "source": source_name,
                "rule": (rule or "")[:120],
                "link": link or "",
                "result": result,
                "queue_type": perf.get("queue_type", ""),
                "queue_start": perf.get("queue_start", 0),
                "queue_wait_ms": perf.get("queue_wait_ms", 0),
                "get_chat_ms": perf.get("get_chat_ms", 0),
                "match_ms": perf.get("match_ms", 0),
                "pre_dedup_ms": perf.get("pre_dedup_ms", perf.get("dedup_ms", 0)),
                "before_send_ms": perf.get("before_send_ms", 0),
                "total_ms": perf.get("total_ms", 0),
                "telegram_delay_sec": perf.get("telegram_delay_sec", 0),
                "slow": self._is_slow_perf(perf),
                "message_time": perf.get("message_time", ""),
                "receive_time": perf.get("receive_time", ""),
                "process_time": perf.get("process_time", ""),
                "send_time": perf.get("send_time", ""),
                "extra": extra or {},
            })
        except Exception:
            pass

    def _verbose_log(self, level: str, message: str, extra: dict | None = None) -> None:
        if LOG_VERBOSE:
            log_line(level, message, extra)

    def _verbose_event(self, kind: str, message: str, extra: dict | None = None) -> None:
        if LOG_VERBOSE:
            push_event(kind, message, extra)

    def _release_pending_dedup(self, code_key: str = "", dedup_profile: dict | None = None) -> None:
        try:
            if code_key:
                r.delete(code_key)
            dedup_id = str((dedup_profile or {}).get("dedup_id") or "")
            if dedup_id:
                release_dedup(dedup_id)
        except Exception:
            pass

    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def worker_status(self) -> dict:
        expected = len(self._worker_specs)
        alive = sum(1 for task in self.workers if task and not task.done())
        failed = sum(1 for task in self.workers if task and task.done() and not task.cancelled())
        return {
            "expected": expected,
            "alive": alive,
            "failed": failed,
            "active_jobs": self._active_jobs,
            "reconnecting": self._reconnect_in_progress,
        }

    async def _wait_for_reconnect(self) -> None:
        while self._reconnect_in_progress:
            await asyncio.sleep(0.05)

    def _create_worker_task(self, client: TelegramClient, spec: tuple[str, int]) -> asyncio.Task:
        kind, worker_id = spec
        if kind == "retry":
            coro = self._retry_failed_queue(client)
        elif kind == "priority":
            coro = self._priority_worker(client, worker_id)
        else:
            coro = self._normal_worker(client, worker_id)
        return asyncio.create_task(coro, name=f"slowlink-{kind}-{worker_id}")

    def _ensure_workers(self, client: TelegramClient) -> None:
        if not self._worker_specs:
            return
        with self.lock:
            current_by_spec = dict(zip(self._worker_specs, self.workers))
            rebuilt = []
            for spec in self._worker_specs:
                task = current_by_spec.get(spec)
                if task is None or task.done():
                    if task is not None and not task.cancelled():
                        try:
                            error = task.exception()
                        except Exception as e:
                            error = e
                        if error:
                            log_line("error", f"工作线程异常退出，正在补建：{spec[0]}-{spec[1]}：{error}")
                    task = self._create_worker_task(client, spec)
                rebuilt.append(task)
            self.workers = rebuilt

    def _retire_worker_spec(self, spec: tuple[str, int]) -> None:
        try:
            with self.lock:
                if spec in self._worker_specs:
                    idx = self._worker_specs.index(spec)
                    self._worker_specs.pop(idx)
                    if idx < len(self.workers):
                        self.workers.pop(idx)
        except Exception:
            pass

    def _reconcile_worker_targets(self, client: TelegramClient) -> None:
        if self.queue is None:
            return
        normal_q = self.queue.qsize()
        priority_q = self.priority_queue.qsize() if self.priority_queue else 0
        now = time.monotonic()
        if (
            normal_q >= WORKER_EXPAND_NORMAL_QUEUE
            or priority_q >= WORKER_EXPAND_PRIORITY_QUEUE
        ):
            target = max(self._base_normal_workers, MAX_NORMAL_WORKERS)
            self._worker_idle_since = None
        else:
            if self._worker_idle_since is None:
                self._worker_idle_since = now
            if (
                normal_q == 0
                and priority_q == 0
                and self._active_jobs == 0
                and now - self._worker_idle_since >= WORKER_SHRINK_IDLE_SECONDS
            ):
                target = max(MIN_NORMAL_WORKERS, self._base_normal_workers - 1)
            else:
                target = self._desired_normal_workers
        target = max(MIN_NORMAL_WORKERS, min(MAX_NORMAL_WORKERS, target))
        if target == self._desired_normal_workers:
            return
        if target > self._desired_normal_workers:
            for worker_id in range(self._desired_normal_workers, target):
                spec = ("normal", worker_id)
                self._worker_specs.append(spec)
                self.workers.append(self._create_worker_task(client, spec))
        self._desired_normal_workers = target

    def start(self) -> str:
        with self.lock:
            if self.is_running():
                set_value("bot_status", "running")
                return "监听已经在运行"
            self.started_ok = False
            self._reconnect_in_progress = False
            self.thread = threading.Thread(target=self._thread_main, daemon=True, name="slowlink-listener")
            self.thread.start()
            self._verbose_log("info", "监听线程已创建，等待 Telegram 连接")
            set_value("bot_status", "starting")
            self._verbose_event("info", "监听启动中")
            return "监听启动中"

    def stop(self) -> str:
        with self.lock:
            if not self.is_running():
                set_value("bot_status", "stopped")
                push_event("warning", "监听未运行，无需停止")
                return "监听未运行"
            try:
                if self.loop and self.stop_event:
                    self.loop.call_soon_threadsafe(self.stop_event.set)
                if self.loop and self.client:
                    # Force disconnect to unblock the update loop immediately.
                    async def _disconnect(c):
                        try:
                            await c.disconnect()
                        except Exception:
                            pass
                    asyncio.run_coroutine_threadsafe(_disconnect(self.client), self.loop)
                push_event("warning", "正在停止监听")
            except Exception as e:
                add_fail({"stage": "stop", "error": str(e)})
                return f"停止请求失败：{e}"

        if self.thread:
            self.thread.join(timeout=10)
        if self.is_running():
            set_value("bot_status", "stopping")
            return "已发送停止请求，后台正在退出"
        set_value("bot_status", "stopped")
        return "监听已停止"

    def _thread_main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.stop_event = asyncio.Event()
        try:
            self._verbose_log("info", "监听线程进入事件循环")
            with SESSION_LOCK:
                self.loop.run_until_complete(self._run())
        except Exception as e:
            set_value("bot_status", "error")
            add_fail({"stage": "runner", "error": str(e)})
            push_event("error", f"监听异常退出：{e}")
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                self.loop.close()
            except Exception:
                pass
            log_line("warning", "监听线程已退出")
            self.loop = None
            self.stop_event = None
            self.client = None
            self.started_ok = False
            if get("bot_status") not in {"error"}:
                set_value("bot_status", "stopped")

    async def _run(self):
        startup_t0 = time.monotonic()
        api_id = int(get("tg_api_id", "0") or 0)
        api_hash = get("tg_api_hash", "") or ""
        if not api_id or not api_hash:
            raise ValueError("未配置 Telegram API")

        client = TelegramClient(SESSION_PATH, api_id, api_hash)
        self.client = client
        self._verbose_log("info", "正在连接 Telegram")
        await client.connect()
        if not await client.is_user_authorized():
            set_value("tg_logged_in", "0")
            raise ValueError("Telegram 未登录，请先在网页完成登录")

        # Warm dialog/entity cache for -100ID resolving.
        self._verbose_log("info", "启动时刷新会话缓存，用于目标快速解析")
        dialogs = await self._refresh_entity_cache(client, force=False)
        dialog_count = len(dialogs) if dialogs else len(get_json("dialog_cache", []) or [])
        self._refresh_monitor_peer_filter()
        if dialogs is not None:
            self._verbose_event("info", f"已刷新会话缓存：{len(dialogs)} 个，快速解析缓存：{len(self.entity_cache)} 个键")
        else:
            self._verbose_event("info", f"已加载持久化实体缓存：{len(self.entity_cache)} 个键")

        me = await client.get_me()
        user = f"@{me.username}" if getattr(me, "username", None) else (getattr(me, "first_name", None) or str(me.id))
        set_value("tg_logged_in", "1")
        set_value("tg_current_user", user)

        self.queue = asyncio.Queue(maxsize=5000)
        self.priority_queue = asyncio.Queue(maxsize=2000)
        target_ok, _target_fail, target_total = await self._warm_targets(client)
        try:
            worker_count = int(get("worker_count", str(LISTENER_WORKERS)) or LISTENER_WORKERS)
        except Exception:
            worker_count = LISTENER_WORKERS
        worker_count = max(2, min(4, worker_count))
        normal_worker_count = worker_count - PRIORITY_WORKER_COUNT
        self._base_normal_workers = max(MIN_NORMAL_WORKERS, normal_worker_count)
        self._desired_normal_workers = self._base_normal_workers
        self._worker_idle_since = time.monotonic()
        self._worker_specs = [("retry", 0), ("priority", 0)]
        self._worker_specs += [("normal", i) for i in range(normal_worker_count)]
        self.workers = [self._create_worker_task(client, spec) for spec in self._worker_specs]
        self._verbose_log("info", f"消息处理线程已启动：优先 {PRIORITY_WORKER_COUNT}，普通 {normal_worker_count}")

        @client.on(events.NewMessage(incoming=True, func=self._fast_event_filter))
        async def handler(event):
            self._flow_counters["handler_entered"] += 1
            try:
                chat2 = event.chat
                if chat2 is not None:
                    from link_builder import normalize_chat_value as _ncv, get_chat_keys as _gck
                    ck = {_ncv(x) for x in _gck(chat2)}
                    ms = self._cached_set("monitor_chats", ttl=60.0)
                    if ms and not (ck & ms):
                        self._flow_counters["fallback_filtered"] += 1
                        return
            except Exception:
                pass
            # Never do heavy matching/sending inside Telethon's update callback.
            # Put the event plus receive timestamp into an internal queue.
            # V1.30: this lets us separate Telegram push delay from internal processing time.
            # V1.35.9: Register/Renew/lottery messages go to priority queue.
            try:
                receive_ts = time.time()
                self._last_message_time = receive_ts
                msg_ts = None
                try:
                    try: _msg = event.message
                    except Exception: _msg = None
                    dt = getattr(_msg, "date", None)
                    if dt is not None:
                        if getattr(dt, "tzinfo", None) is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        msg_ts = float(dt.timestamp())
                except Exception:
                    msg_ts = None
                payload = {"event": event, "enqueue_ts": receive_ts, "receive_ts": receive_ts, "message_ts": msg_ts}
                # Quick priority check uses the same plain/RichMessage extraction as matching.
                try: raw_text = get_text(event.message)
                except Exception: raw_text = ""
                low_text = raw_text.lower()
                is_priority = any(k in low_text for k in ["register", "renew", "whitelist", "抽奖", "开放注册", "自由注册", "开注", "邀请码", "注册码", "已为您生成", "总注册限制", "已注册人数", "剩余可注册", "开启注册", "定时注册"])
                if is_priority and self.priority_queue:
                    self.priority_queue.put_nowait(payload)
                    self._flow_counters["enqueued"] += 1
                elif self.queue:
                    self.queue.put_nowait(payload)
                    self._flow_counters["enqueued"] += 1
            except asyncio.QueueFull:
                self._flow_counters["queue_full"] += 1
                add_fail({"stage": "queue", "error": "消息队列已满，丢弃一条新消息"})
                push_event("error", "消息队列已满，可能监听源过多或正则过慢")

        self.started_ok = True
        set_value("bot_status", "running")
        startup_elapsed = time.monotonic() - startup_t0
        startup_summary = f"监听已启动：会话 {dialog_count}，缓存 {len(self.entity_cache)}，目标 {len(target_ok)}/{target_total}，线程 优先 {PRIORITY_WORKER_COUNT}/普通 {normal_worker_count}，耗时 {startup_elapsed:.1f}s"
        push_event("success", startup_summary)

        # Keep the Telethon connection alive. We don't use run_until_disconnected
        # because the stop button needs an explicit event.
        last_heartbeat_store = 0.0
        while self.stop_event and not self.stop_event.is_set():
            if not client.is_connected():
                log_line("warning", "Telegram 连接断开，尝试重连")
                try:
                    await client.connect()
                    log_line("info", "Telegram 重连成功")
                except Exception as e:
                    log_line("error", f"Telegram 重连失败: {e}")
                    await asyncio.sleep(5)
                    continue
            now = time.time()
            monotonic_now = time.monotonic()
            if self._monitor_filter_dirty or monotonic_now - self._monitor_filter_refresh_ts >= 60:
                self._refresh_monitor_peer_filter()
            if self._entity_cache_refreshed_ts and monotonic_now - self._entity_cache_refreshed_ts >= 86400:
                await self._refresh_entity_cache(client, force=True)
                self._monitor_filter_dirty = True
                self._refresh_monitor_peer_filter()
            self._publish_flow_stats(monotonic_now)
            self._reconcile_worker_targets(client)
            self._ensure_workers(client)
            # Proactive reconnect every 2h to keep connection fresh with less churn.
            if now - self._last_delay_reconnect_ts >= 7200 and not self._reconnect_requested and self._telegram_delay_high_count == 0:
                q = self.queue.qsize() if self.queue else 0
                pq = self.priority_queue.qsize() if self.priority_queue else 0
                if q == 0 and pq == 0 and self._active_jobs == 0:
                    self._reconnect_requested = True
                    self._reconnect_reason = "_proactive_"
            # No message for 30 min -> reconnect.
            if (
                self._last_message_time
                and now - max(self._last_message_time, self._last_delay_reconnect_ts) > 1800
                and not self._reconnect_requested
            ):
                self._reconnect_requested = True
                self._reconnect_reason = "连续 30 分钟未收到消息"
                push_event("warning", f"{self._reconnect_reason}，准备轻量重连")
            if now - last_heartbeat_store >= 120:
                last_heartbeat_store = now
                set_value("listener_heartbeat_ts", str(int(now)))
            if now - self._last_trim_ts >= 60:
                self._last_trim_ts = now
                self._trim_caches()
            if now - self._last_heartbeat_log >= 1800:
                self._last_heartbeat_log = now
                q = self.queue.qsize() if self.queue else 0
                pq = self.priority_queue.qsize() if self.priority_queue else 0
                log_line("info", f"监听心跳正常，普通队列 {q}，优先队列 {pq}")
            if self._reconnect_requested:
                self._reconnect_in_progress = True
                try:
                    while self._active_jobs > 0 and self.stop_event and not self.stop_event.is_set():
                        await asyncio.sleep(0.05)
                    if self.stop_event and not self.stop_event.is_set():
                        await self._reconnect_listener_client(client)
                finally:
                    self._reconnect_in_progress = False
            await asyncio.sleep(2)

        if not client.is_connected() and self.stop_event and not self.stop_event.is_set():
            add_fail({"stage": "listener_connection", "error": "Telegram 连接断开，监听循环退出"})
            push_event("error", "Telegram 连接断开，监听已退出，请重新启动监听")

        for task in list(self.workers):
            task.cancel()
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []
        self._worker_specs = []
        self.queue = None
        self.priority_queue = None

        try:
            await client.disconnect()
        except Exception:
            pass
        push_event("warning", "监听已停止")


    async def _warm_targets(self, client: TelegramClient):
        """Pre-resolve send targets once at startup.

        Forwarding must not refresh all dialogs or resolve -100ID on the hot path.
        If a target cannot be cached here, the real send path will try a quick
        cache-only resolve and then fail fast instead of blocking messages for
        seconds.
        """
        self._target_entity_cache.clear()
        targets = split_dialog_values(get("target_chat", "") or "")
        if not targets:
            return [], [], 0
        ok, fail = [], []
        self._verbose_log("info", f"开始预缓存转发目标：{targets}")
        for target in targets:
            cache_key = normalize_chat_value(target)
            try:
                entity = await resolve_entity(client, target, cache=self.entity_cache, refresh=False)
                self._target_entity_cache[cache_key] = entity
                ok.append(target)
            except Exception as e:
                fail.append(f"{target}: {e}")
        if ok:
            self._verbose_event("success", "转发目标已预缓存：" + ", ".join(ok[:5]))
        if fail:
            add_fail({"stage": "target_cache", "error": "；".join(fail[:3])})
            push_event("warning", "部分转发目标预缓存失败，发送时会快速失败，不再刷新全部群")
        return ok, fail, len(targets)

    def _record_telegram_delay(self, delay_sec: float, source_name: str):
        """Track consecutive high Telegram push delays and request reconnect if needed.

        Responsive thresholds: one >=60s delay or two consecutive >=30s delays
        request a light reconnect, with a cooldown to avoid churn.
        """
        now = time.time()
        cooldown_ok = now - self._last_delay_reconnect_ts >= TELEGRAM_DELAY_RECONNECT_COOLDOWN_SECONDS

        if delay_sec >= TELEGRAM_DELAY_IMMEDIATE_RECONNECT_SECONDS:
            self._telegram_delay_high_count += 1
            if cooldown_ok and not self._reconnect_requested:
                self._reconnect_requested = True
                self._reconnect_reason = f"单次 Telegram 推送延迟 {delay_sec:.1f}s，来源 {source_name}"
                push_event("warning", f"{self._reconnect_reason}，准备轻量重连")
                self._telegram_delay_high_count = 0
            else:
                push_event("warning", f"Telegram 推送高延迟 {delay_sec:.1f}s，连续 {self._telegram_delay_high_count} 次，来源 {source_name}")
            return

        if delay_sec >= TELEGRAM_DELAY_HIGH_SECONDS:
            self._telegram_delay_high_count += 1
            if (
                self._telegram_delay_high_count >= TELEGRAM_DELAY_RECONNECT_COUNT
                and cooldown_ok
                and not self._reconnect_requested
            ):
                self._reconnect_requested = True
                self._reconnect_reason = f"Telegram 连续 {self._telegram_delay_high_count} 次推送高延迟，最近 {delay_sec:.1f}s，来源 {source_name}"
                push_event("warning", f"{self._reconnect_reason}，准备轻量重连")
            else:
                push_event("warning", f"Telegram 推送高延迟 {delay_sec:.1f}s，连续 {self._telegram_delay_high_count} 次，来源 {source_name}")
        elif delay_sec < 10:
            # A normal timely update means the push stream has recovered.
            if self._telegram_delay_high_count:
                log_line("info", "Telegram 推送延迟恢复正常，高延迟计数已清零")
            self._telegram_delay_high_count = 0

    async def _reconnect_listener_client(self, client: TelegramClient):
        """Reconnect Telethon client when the update stream looks stale.

        Event handlers stay registered on the same client. This only reconnects
        the transport; dialog and target caches are reused, and stale entities
        are refreshed on the send path when a target fails.
        """
        reason = self._reconnect_reason or "Telegram 推送延迟过高"
        proactive = reason == "_proactive_"
        self._reconnect_requested = False
        self._reconnect_reason = ""
        self._dedup_settings = (0.0, True, "strict", 20, 20)  # (ts, enabled, mode, other_minutes, code_minutes)
        self._last_delay_reconnect_ts = time.time()
        if proactive:
            log_line("info", "定期刷新连接（2h）")
        try:
            try:
                await client.disconnect()
            except Exception as e:
                push_event("warning", f"轻量重连断开旧连接时出现异常：{e}")
            await asyncio.sleep(1)
            await client.connect()
            if not await client.is_user_authorized():
                set_value("tg_logged_in", "0")
                raise ValueError("Telegram 未登录，自动重连失败")
            now = int(time.time())
            set_value("listener_heartbeat_ts", str(now))
            self._telegram_delay_high_count = 0
            if not proactive:
                push_event("success", "轻量重连完成，复用已有缓存")
        except Exception as e:
            add_fail({"stage": "delay_reconnect", "error": str(e)})
            push_event("error", f"自动重连监听失败：{e}")

    async def _priority_worker(self, client: TelegramClient, worker_id: int):
        while True:
            try:
                if not self.priority_queue:
                    await asyncio.sleep(0.1)
                    continue
                await self._wait_for_reconnect()
                item = await self.priority_queue.get()
                await self._wait_for_reconnect()
                self._active_jobs += 1
                try:
                    if isinstance(item, dict):
                        meta = item
                        meta["queue_type"] = "priority"
                        await self._handle_message(client, meta.get("event"), meta)
                    else:
                        await self._handle_message(client, item, {"queue_type": "priority"})
                finally:
                    self._active_jobs = max(0, self._active_jobs - 1)
                    self.priority_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                add_fail({"stage": f"priority_worker:{worker_id}", "error": str(e)})
                push_event("error", f"优先转发工作线程异常：{e}")

    async def _normal_worker(self, client: TelegramClient, worker_id: int):
        while True:
            try:
                if not self.queue:
                    await asyncio.sleep(0.1)
                    continue
                if (
                    worker_id >= (self._desired_normal_workers or 1)
                    and self.queue.qsize() == 0
                    and (self.priority_queue is None or self.priority_queue.qsize() == 0)
                ):
                    self._retire_worker_spec(("normal", worker_id))
                    break
                await self._wait_for_reconnect()
                item = await self.queue.get()
                await self._wait_for_reconnect()
                self._active_jobs += 1
                try:
                    if isinstance(item, dict):
                        meta = item
                        meta["queue_type"] = "normal"
                        await self._handle_message(client, meta.get("event"), meta)
                    else:
                        await self._handle_message(client, item, {"queue_type": "normal"})
                finally:
                    self._active_jobs = max(0, self._active_jobs - 1)
                    self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                add_fail({"stage": f"normal_worker:{worker_id}", "error": str(e)})
                push_event("error", f"普通转发工作线程异常：{e}")

    async def _handle_message(self, client: TelegramClient, event, meta: dict | None = None):
        meta = meta or {}
        t0 = time.monotonic()
        process_ts = time.time()
        queue_type = str(meta.get("queue_type") or "")
        if queue_type == "priority":
            qsize_at_start = self.priority_queue.qsize() if self.priority_queue else 0
        else:
            qsize_at_start = self.queue.qsize() if self.queue else 0
        receive_ts = float(meta.get("receive_ts") or process_ts)
        enqueue_ts = float(meta.get("enqueue_ts") or receive_ts)
        message_ts = meta.get("message_ts")
        try:
            message_ts = float(message_ts) if message_ts is not None else None
        except Exception:
            message_ts = None
        telegram_delay_sec = max(0.0, receive_ts - message_ts) if message_ts else 0.0
        queue_wait_ms = max(0, int((process_ts - enqueue_ts) * 1000))
        perf = {
            "queue_start": qsize_at_start,
            "queue_wait_ms": queue_wait_ms,
            "telegram_delay_sec": round(telegram_delay_sec, 3),
            "queue_type": queue_type,
        }
        try:
            try:
                chat = event.chat
                if chat is None:
                    chat = await event.get_chat()
            except Exception:
                return
            perf["get_chat_ms"] = int((time.monotonic() - t0) * 1000)
            chat_keys = {normalize_chat_value(x) for x in get_chat_keys(chat)}
            monitors = self._cached_set("monitor_chats", ttl=60.0)
            if not monitors or not (chat_keys & monitors):
                return

            try: _evt_msg = event.message
            except Exception: _evt_msg = None
            text = get_text(_evt_msg)
            analysis = analyze_message(text)
            matched = bool(analysis.get("matched"))
            rule = str(analysis.get("rule") or "")
            perf["match_ms"] = int((time.monotonic() - t0) * 1000)
            if not matched:
                return
            self._flow_counters["matched"] += 1
            perf.update({
                "message_time": format_time(message_ts) if message_ts else "",
                "receive_time": format_time(receive_ts),
                "process_time": format_time(process_ts),
            })
            code_detail = analysis.get("code_detail") or {}
            source_name = self._source_name(chat)

            # Check excludes before recording hit: prevent misleading "hit" entries
            excludes = self._cached_set("exclude_chats", ttl=60.0)
            if excludes and (chat_keys & excludes):
                add_hit({"source": source_name, "rule": rule[:120], "status": "排除来源，命中但未转发"})
                push_event("warning", f"命中但来源已排除：{source_name}")
                return

            self._record_telegram_delay(telegram_delay_sec, source_name)

            target_raw = self._cached_str("target_chat")
            targets = split_dialog_values(target_raw)
            if not targets:
                add_fail({"stage": "send", "error": "未设置转发目标"})
                push_event("error", "命中但未设置转发目标")
                return

            try:
                _evt_msg2 = event.message
            except Exception:
                _evt_msg2 = None
            link = build_message_link(chat, _evt_msg2, self._cached_str("public_link_domain")) if _evt_msg2 else ""
            dedup_enabled, mode, _dedup_other, code_minutes = self._cached_dedup_settings()
            reserved_code_key = ""
            dedup_profile = None

            # Layer 0: same invite code already seen -> block (even if text differs)
            code_identity = code_detail.get("identity") or ""
            if code_identity:
                code_key = "dedup:code:" + sha(code_identity)
                code_ttl = max(60, code_minutes * 60)
                is_new = r.set(code_key, "1", ex=code_ttl, nx=True)
                if not is_new:
                    elapsed = time.monotonic() - t0
                    perf["total_ms"] = int(elapsed * 1000)
                    message = f"重复跳过：相同邀请码已转发（{code_identity[:16]}），内部耗时 {elapsed:.2f}s"
                    add_hit({"source": source_name, "rule": rule[:120], "link": link, "status": message, "perf": perf})
                    self._record_perf_event(source_name, rule, link, "duplicate_code", perf, {"code": code_identity[:32]})
                    event_message = f"{message}：{link}" if link else message
                    push_event("info", event_message)
                    return
                reserved_code_key = code_key

            if dedup_enabled:
                duplicate, reason, dedup_profile = check_and_mark(text, link, None, mode, source_name)
                perf["pre_dedup_ms"] = int((time.monotonic() - t0) * 1000)
                if duplicate:
                    self._release_pending_dedup(reserved_code_key)
                    elapsed = time.monotonic() - t0
                    perf["total_ms"] = int(elapsed * 1000)
                    add_hit({"source": source_name, "rule": rule[:120], "link": link, "status": f"重复跳过：{reason}；耗时 {elapsed:.2f}s", "perf": perf})
                    self._record_perf_event(source_name, rule, link, "duplicate", perf, {"reason": reason})
                    push_event("info", f"重复跳过：{reason}")
                    return

            sent = []
            failed = []
            perf["before_send_ms"] = int((time.monotonic() - t0) * 1000)
            # Parallel sends to all targets (each has its own retry + jitter)
            async def _send_one_target(target):
                try:
                    await self._send_with_retry(client, target, link)
                    return (True, target, None)
                except Exception as e:
                    add_fail({"stage": "send", "target": target, "error": str(e), "link": link})
                    return (False, target, str(e))
            results = await asyncio.gather(*[_send_one_target(t) for t in targets], return_exceptions=True)
            for item in results:
                if isinstance(item, BaseException):
                    name = type(item).__name__
                    if isinstance(item, asyncio.CancelledError):
                        # Reconnect interrupted us; message likely already sent ? do NOT re-queue
                        failed.append(f"gather_cancelled: {name}")
                    else:
                        detail = str(item) or name
                        failed.append(f"gather_error: {detail}")
                        try:
                            import json as _json2
                            for _t in targets:
                                r.lpush("failed_queue", _json2.dumps({"text": link, "target": _t, "ts": int(time.time()), "reason": detail}, ensure_ascii=False))
                                r.ltrim("failed_queue", 0, 99)
                        except Exception:
                            pass
                elif item[0]:
                    sent.append(item[1])
                else:
                    failed.append(f"{item[1]}: {item[2]}")

            elapsed = time.monotonic() - t0
            send_ts = time.time()
            perf["total_ms"] = int(elapsed * 1000)
            perf["send_time"] = format_time(send_ts)
            code_label = ""
            if code_detail:
                code_label = f"，码规则：{code_detail.get('name')}"
            # Code-level dedup already marked atomically above

            if sent:
                self._flow_counters["forwarded"] += 1
                status = "已转发：" + ", ".join(sent) + f"；内部耗时 {elapsed:.2f}s"
                if telegram_delay_sec >= 2:
                    status += f"；Telegram推送延迟 {telegram_delay_sec:.1f}s"
                if queue_wait_ms >= 1000:
                    status += f"；队列等待 {queue_wait_ms/1000:.1f}s"
                if qsize_at_start:
                    status += f"；队列开始 {qsize_at_start}"
                if code_detail:
                    status += f"；完整码：{code_detail.get('code','')}"
                if elapsed >= 2:
                    status += f"；慢转发"
                add_hit({"source": source_name, "rule": rule[:120], "link": link, "status": status, "perf": perf, "code": code_detail.get('code','') if code_detail else '', "message_time": perf.get("message_time"), "receive_time": perf.get("receive_time"), "send_time": perf.get("send_time"), "telegram_delay_sec": round(telegram_delay_sec, 3), "internal_total_ms": perf.get("total_ms")})
                self._record_perf_event(source_name, rule, link, "sent", perf, {"sent_count": len(sent), "failed_count": len(failed)})
                push_event("success", f"命中并转发：{link}，内部耗时 {elapsed:.2f}s，Telegram延迟 {telegram_delay_sec:.1f}s")
                if elapsed >= 2:
                    add_fail({"stage": "slow_forward", "error": f"内部慢转发 {elapsed:.2f}s，队列开始={qsize_at_start}，queue_wait={queue_wait_ms}ms，match={perf.get('match_ms',0)}ms，pre_dedup={perf.get('pre_dedup_ms',0)}ms，send_start={perf.get('before_send_ms',0)}ms{code_label}", "link": link, "perf": perf})
                    push_event("warning", f"内部慢转发 {elapsed:.2f}s：{source_name}{code_label}")
            if failed and not sent:
                self._release_pending_dedup(reserved_code_key, dedup_profile)
                add_hit({"source": source_name, "rule": rule[:120], "link": link, "status": "命中但发送失败"})
                self._record_perf_event(source_name, rule, link, "send_failed", perf, {"failed": failed[:3]})
                push_event("error", "命中但发送失败：" + " | ".join(failed[:2]))
        except FloodWaitError as e:
            add_fail({"stage": "floodwait", "error": f"FloodWait {e.seconds}s；不再长时间卡住转发队列"})
            if int(getattr(e, "seconds", 0) or 0) <= 3:
                await asyncio.sleep(int(e.seconds))
        except Exception as e:
            add_fail({"stage": "handle_message", "error": str(e)})
            push_event("error", f"处理消息失败：{e}")

    def _source_name(self, chat) -> str:
        cid = getattr(chat, "id", None)
        key = str(cid or 0)
        cached = self._source_cache.get(key)
        if cached:
            return cached
        title = getattr(chat, "title", None)
        username = getattr(chat, "username", None)
        if title:
            name = title
        elif username:
            name = "@" + username
        else:
            name = str(cid or "")
        if len(self._source_cache) > 300:
            self._source_cache.pop(next(iter(self._source_cache)))
        self._source_cache[key] = name
        return name

    async def _send_one(self, client: TelegramClient, target: str, text: str):
        """Fast send path for real forwarding.

        V1.28: the hot path never calls get_dialogs()/refresh.
        Targets are warmed at listener startup.
        """
        cache_key = normalize_chat_value(target)
        cached_entity = self._target_entity_cache.get(cache_key)
        last_error = None

        if cached_entity is not None:
            try:
                return await client.send_message(cached_entity, text)
            except FloodWaitError as e:
                sec = int(getattr(e, "seconds", 0) or 0)
                if sec <= 60:
                    await asyncio.sleep(sec)
                    return await client.send_message(cached_entity, text)
                raise
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                if any(k in err_str for k in ("forbidden","not a member","403","unauthorized","not found","invalid")):
                    self._target_entity_cache.pop(cache_key, None)

        try:
            entity = await resolve_entity(client, target, cache=self.entity_cache, refresh=False)
            self._target_entity_cache[cache_key] = entity
            return await client.send_message(entity, text)
        except FloodWaitError as e:
            if int(getattr(e, "seconds", 0) or 0) <= 3:
                await asyncio.sleep(int(e.seconds))
                entity = self._target_entity_cache.get(cache_key) or await resolve_entity(client, target, cache=self.entity_cache, refresh=False)
                self._target_entity_cache[cache_key] = entity
                return await client.send_message(entity, text)
            raise
        except Exception as e:
            last_error = e

        raise last_error or RuntimeError("发送失败")

    async def _send_with_retry(self, client: TelegramClient, target: str, text: str, max_retries: int = 3):
        last_error = None
        refreshed = False
        cache_key = normalize_chat_value(target)
        for attempt in range(max_retries):
            if attempt > 0:
                jitter = random.uniform(0.8, 1.2)
                await asyncio.sleep(2 ** attempt * jitter)
            try:
                return await self._send_one(client, target, text)
            except FloodWaitError as e:
                sec = int(getattr(e, "seconds", 0) or 0)
                if sec <= 30:
                    await asyncio.sleep(sec)
                    try:
                        return await self._send_one(client, target, text)
                    except Exception as e2:
                        last_error = e2
                        continue
                elif sec <= 300:
                    await asyncio.sleep(min(sec, 120))
                # Queue for later recovery instead of losing the message
                r.lpush("failed_queue", _json.dumps({"text": text, "target": target, "ts": int(time.time()), "reason": f"FloodWait {sec}s"}, ensure_ascii=False))
                r.ltrim("failed_queue", 0, 99)
                raise
            except Exception as e:
                last_error = e
                if not refreshed:
                    refreshed = True
                    try:
                        await self._refresh_entity_cache(client, force=True)
                        self._target_entity_cache.pop(cache_key, None)
                    except Exception:
                        pass
        # All retries exhausted - write to failed queue for background recovery
        try:
            r.lpush("failed_queue", _json.dumps({"text": text, "target": target, "ts": int(time.time())}, ensure_ascii=False))
            r.ltrim("failed_queue", 0, 99)
        except Exception:
            pass
        raise last_error or RuntimeError("send failed after retries")

    async def _retry_failed_queue(self, client: TelegramClient):
        while not self.stop_event.is_set():
            try:
                await self._wait_for_reconnect()
                # Atomic pop + send: _send_with_retry already re-queues on failure
                # (FloodWait handler does r.lpush). Do NOT push back here
                # to avoid double-queue (same message forwarded twice).
                raw = r.rpop("failed_queue")
                if raw:
                    item = _json.loads(raw)
                    self._active_jobs += 1
                    try:
                        await self._send_with_retry(client, str(item.get("target","")), str(item.get("text","")), 2)
                    finally:
                        self._active_jobs = max(0, self._active_jobs - 1)
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            await asyncio.sleep(30)

    def _trim_caches(self):
        now = time.time()  # wall clock for cross-thread consistency
        # _set_cache stores (ts, set) tuples
        for k in list(self._set_cache):
            v = self._set_cache.get(k)
            if isinstance(v, tuple) and now - v[0] > 300:
                self._set_cache.pop(k, None)
        if len(self._set_cache) > 500:
            excess = len(self._set_cache) - 300
            for k in list(self._set_cache)[:excess]:
                self._set_cache.pop(k, None)
        # _target_entity_cache stores Telethon entities directly (no timestamp)
        if len(self._target_entity_cache) > 500:
            for k in list(self._target_entity_cache)[:200]:
                self._target_entity_cache.pop(k, None)
        # _source_cache stores strings (name)
        if len(self._source_cache) > 300:
            for k in list(self._source_cache)[:100]:
                self._source_cache.pop(k, None)

    def _run_on_listener(self, coro, timeout: int = 25):
        if not self.is_running() or not self.loop or not self.client:
            return None
        future = asyncio.run_coroutine_threadsafe(coro(self.client), self.loop)
        return future.result(timeout=timeout)

    async def _test_send_with_client(self, client: TelegramClient, target_raw: str) -> str:
        if not await client.is_user_authorized():
            raise ValueError("Telegram 未登录")
        self._verbose_log("info", "启动时刷新会话缓存，用于目标快速解析")
        await self._refresh_entity_cache(client, force=True)
        self._monitor_filter_dirty = True
        targets = split_dialog_values(target_raw)
        if not targets:
            raise ValueError("请先设置转发目标")
        ok = []
        fail = []
        for target in targets:
            try:
                entity = await resolve_entity(client, target, cache=self.entity_cache, refresh=True)
                await client.send_message(entity, "✅ 测试发送成功")
                ok.append(target)
            except Exception as e:
                fail.append(f"{target}: {e}")
        if ok:
            push_event("success", "测试发送成功：" + ", ".join(ok))
            return "测试发送成功：" + ", ".join(ok)
        raise ValueError("；".join(fail) or "测试发送失败")

    async def test_send(self) -> str:
        api_id = int(get("tg_api_id", "0") or 0)
        api_hash = get("tg_api_hash", "") or ""
        target = get("target_chat", "") or ""
        if not target:
            raise ValueError("请先设置转发目标")

        if self.is_running() and self.loop and self.client:
            return self._run_on_listener(lambda c: self._test_send_with_client(c, target))

        with SESSION_LOCK:
            client = TelegramClient(SESSION_PATH, api_id, api_hash)
            await client.connect()
            try:
                return await self._test_send_with_client(client, target)
            finally:
                await client.disconnect()

    async def _test_source_with_client(self, client: TelegramClient, source: str) -> str:
        if not await client.is_user_authorized():
            raise ValueError("Telegram 未登录")
        self._verbose_log("info", "启动时刷新会话缓存，用于目标快速解析")
        await self._refresh_entity_cache(client, force=True)
        self._monitor_filter_dirty = True
        entity = await resolve_entity(client, source, cache=self.entity_cache, refresh=True)
        title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or "未知"
        username = getattr(entity, "username", None)
        eid = getattr(entity, "id", None)
        fill_id = f"-100{eid}" if eid else ""
        msg = f"可访问：{title}"
        if username:
            msg += f" / @{username}"
        if fill_id:
            msg += f" / 可填：{fill_id}"
        push_event("success", f"测试监听源成功：{msg}")
        return msg

    async def test_source(self, source: str) -> str:
        api_id = int(get("tg_api_id", "0") or 0)
        api_hash = get("tg_api_hash", "") or ""
        if not source:
            raise ValueError("来源为空")

        if self.is_running() and self.loop and self.client:
            return self._run_on_listener(lambda c: self._test_source_with_client(c, source))

        with SESSION_LOCK:
            client = TelegramClient(SESSION_PATH, api_id, api_hash)
            await client.connect()
            try:
                return await self._test_source_with_client(client, source)
            finally:
                await client.disconnect()

    async def _list_dialogs_with_client(self, client: TelegramClient) -> list[dict]:
        """Return all groups/channels visible to the logged-in account.

        This is used by the WebUI monitor panel so the user does not need to
        manually type every chat id. We intentionally skip private user dialogs.
        """
        if not await client.is_user_authorized():
            raise ValueError("Telegram 未登录")
        self._verbose_log("info", "启动时刷新会话缓存，用于目标快速解析")
        dialogs = await self._refresh_entity_cache(client, force=True)
        self._monitor_filter_dirty = True
        out: list[dict] = []
        seen: set[str] = set()
        for dialog in dialogs:
            entity = dialog.entity
            # Skip private user dialogs. Keep channels, supergroups and normal groups.
            if getattr(entity, "bot", False) or (hasattr(entity, "first_name") and not getattr(entity, "title", None)):
                continue
            title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(getattr(dialog, "name", "未知会话"))
            username = getattr(entity, "username", None)
            eid = getattr(entity, "id", None)
            did = getattr(dialog, "id", None)
            is_channel = bool(getattr(entity, "broadcast", False))
            is_supergroup = bool(getattr(entity, "megagroup", False))
            if is_channel:
                dtype = "频道"
            elif is_supergroup:
                dtype = "群组"
            else:
                dtype = "群组"
            if eid is not None:
                value = f"-100{eid}" if (is_channel or is_supergroup) else str(did or eid)
            elif did is not None:
                value = str(did)
            elif username:
                value = f"@{username}"
            else:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "title": title,
                "username": f"@{username}" if username else "",
                "id": str(did or eid or ""),
                "value": value,
                "type": dtype,
            })
        out.sort(key=lambda x: (x.get("type", ""), x.get("title", "").lower()))
        return out

    async def list_dialogs(self, force: bool = False) -> list[dict]:
        api_id = int(get("tg_api_id", "0") or 0)
        api_hash = get("tg_api_hash", "") or ""
        if not api_id or not api_hash:
            raise ValueError("未配置 Telegram API")

        # V1.29 fix:
        # - The WebUI "刷新全部群/频道" must really rebuild dialog_cache.
        # - V1.28 returned the old Redis dialog_cache while listening was running.
        #   After the user cleared session/cache, that old cache was empty, so refresh
        #   instantly showed 0 groups.
        # - For a manual refresh we run get_dialogs on the existing listener client.
        #   It may take a few seconds, but it will not open a second SQLite session and
        #   it will not silently return an empty cache.
        if self.is_running() and self.loop and self.client:
            cached = get_json("dialog_cache", []) or []
            if cached and not force:
                push_event("info", f"使用已有会话缓存：{len(cached)} 个群/频道")
                return cached
            push_event("warning", "正在刷新全部群/频道，请稍等；这次会重建会话缓存，不再返回空列表")
            result = self._run_on_listener(lambda c: self._list_dialogs_with_client(c), timeout=90)
            if result is None:
                raise ValueError("监听连接不可用，无法刷新会话列表")
            return result

        with SESSION_LOCK:
            client = TelegramClient(SESSION_PATH, api_id, api_hash)
            await client.connect()
            try:
                return await self._list_dialogs_with_client(client)
            finally:
                await client.disconnect()


manager = BotManager()
