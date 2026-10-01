"""Generic in-process hook loader for SlowLink rule plugins.

The core only knows this small hook contract. Business-specific matching,
dedup, and rule generation live in the active plugin's optional ``hooks.py``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path
from typing import Any


HOOK_ERROR_LOG_INTERVAL_SECONDS = 60.0
_MODULE_CACHE: dict[str, tuple[int, Any]] = {}
_ERROR_CACHE: dict[tuple[str, str], float] = {}
_PLUGIN_MODULE_CACHE: dict[tuple[str, str], tuple[int, tuple[int, ...], Any]] = {}


def _ensure_core_path() -> None:
    core_root = str(Path(__file__).resolve().parent)
    if core_root not in sys.path:
        sys.path.insert(0, core_root)


def _module_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def invalidate(plugin_id: str | None = None) -> None:
    if plugin_id:
        _MODULE_CACHE.pop(plugin_id, None)
        for key in list(_PLUGIN_MODULE_CACHE):
            if key[0] == plugin_id:
                _PLUGIN_MODULE_CACHE.pop(key, None)
        for key in list(_ERROR_CACHE):
            if key[0] == plugin_id:
                _ERROR_CACHE.pop(key, None)
        return
    _MODULE_CACHE.clear()
    _PLUGIN_MODULE_CACHE.clear()
    _ERROR_CACHE.clear()


def _hook_path(plugin_id: str) -> Path:
    from plugin_registry import plugin_dir

    return plugin_dir(plugin_id) / "hooks.py"


def _load_hooks(plugin_id: str):
    _ensure_core_path()
    path = _hook_path(plugin_id)
    if not path.is_file():
        return None
    mtime = path.stat().st_mtime_ns
    cached = _MODULE_CACHE.get(plugin_id)
    if cached and cached[0] == mtime:
        return cached[1]

    module_name = f"slowlink_plugin_hooks_{_module_token(plugin_id)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    _MODULE_CACHE[plugin_id] = (mtime, module)
    return module


def _log_hook_error(plugin_id: str, hook_name: str, exc: Exception) -> None:
    key = (plugin_id, hook_name)
    now = time.monotonic()
    if now - _ERROR_CACHE.get(key, 0.0) < HOOK_ERROR_LOG_INTERVAL_SECONDS:
        return
    _ERROR_CACHE[key] = now
    try:
        from redis_store import log_line

        log_line(
            "warning",
            f"插件钩子执行失败，已使用核心回退：{plugin_id}.{hook_name}: {exc}",
        )
    except Exception:
        pass


def _runtime_fingerprint() -> tuple[int, ...]:
    return tuple(
        id(sys.modules.get(name))
        for name in ("redis_store", "plugin_registry", "code_rules")
    )


def call_hook(hook_name: str, payload: dict[str, Any] | None = None, default=None):
    """Execute an optional plugin hook and isolate failures from the core."""
    name = str(hook_name or "").strip()
    if not name:
        return default
    try:
        from plugin_registry import active_plugin_id

        plugin_id = active_plugin_id()
        if not plugin_id:
            return default
        module = _load_hooks(plugin_id)
        if module is None:
            return default
        hook = getattr(module, name, None)
        if not callable(hook):
            return default
        return hook(dict(payload or {}))
    except Exception as exc:
        try:
            _log_hook_error(plugin_id, name, exc)
        except UnboundLocalError:
            pass
        return default


def has_hook(hook_name: str) -> bool:
    try:
        from plugin_registry import active_plugin_id

        plugin_id = active_plugin_id()
        if not plugin_id:
            return False
        module = _load_hooks(plugin_id)
        return bool(module is not None and callable(getattr(module, hook_name, None)))
    except Exception:
        return False


def load_plugin_module(module_name: str):
    """Load an optional sibling module from the active trusted plugin package."""
    _ensure_core_path()
    name = str(module_name or "").strip()
    if not name or not name.replace("_", "").isalnum():
        raise ValueError("插件模块名无效")
    from plugin_registry import active_plugin_id, plugin_dir

    plugin_id = active_plugin_id()
    if not plugin_id:
        return None
    cache_key = (plugin_id, name)
    path = plugin_dir(plugin_id) / f"{name}.py"
    if not path.is_file():
        return None
    mtime = path.stat().st_mtime_ns
    fingerprint = _runtime_fingerprint()
    cached = _PLUGIN_MODULE_CACHE.get(cache_key)
    if cached and cached[0] == mtime and cached[1] == fingerprint:
        return cached[2]

    full_name = f"slowlink_plugin_{_module_token(plugin_id)}_{_module_token(name)}"
    spec = importlib.util.spec_from_file_location(full_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(full_name, None)
        raise
    _PLUGIN_MODULE_CACHE[cache_key] = (mtime, fingerprint, module)
    return module
