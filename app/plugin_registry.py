"""Plugin registry for SlowLink rule packs.

The core engine stays generic. Built-in matching/dedup knowledge lives in
versioned plugin packages under ``app/plugins/<plugin_id>/``. A plugin is a
small zip containing ``plugin.json`` (manifest) and ``rules.json`` (data).
"""

import io
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

from config import APP_VERSION


PLUGIN_ROOT = Path(__file__).resolve().parent / "plugins"
UPLOAD_ROOT = Path(os.getenv("SLOWLINK_UPLOAD_PLUGIN_DIR", str(PLUGIN_ROOT / "user"))).resolve()
DEFAULT_PLUGIN = "builtin"
ACTIVE_PLUGIN_KEY = "active_plugin"
PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_PLUGIN_BYTES = 10 * 1024 * 1024

_RULES_CACHE: dict[str, dict] = {}
_MANIFEST_CACHE: dict[str, dict] = {}


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = str(value or "").strip().lstrip("v").split(".")
    nums = []
    for part in parts[:3]:
        if not part.isdigit():
            raise ValueError("invalid version part")
        nums.append(int(part))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def _pure_mode() -> bool:
    return os.getenv("SLOWLINK_PURE_MODE", "0") == "1"


def _redis_value(key: str, default=None):
    if _pure_mode():
        return default
    try:
        from redis_store import get
        value = get(key, None)
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def active_plugin_id() -> str:
    if _pure_mode():
        return ""
    env_id = (os.getenv("SLOWLINK_ACTIVE_PLUGIN") or "").strip()
    if env_id:
        if env_id in {"", "none", "off"}:
            return ""
        return env_id if (plugin_dir(env_id) / "plugin.json").exists() else ""
    redis_id = _redis_value(ACTIVE_PLUGIN_KEY, None)
    if redis_id is None:
        return DEFAULT_PLUGIN if _builtin_available() else ""
    redis_id = str(redis_id).strip()
    if redis_id in {"", "none", "off"}:
        return ""
    return redis_id if (plugin_dir(redis_id) / "plugin.json").exists() else ""


def _builtin_available() -> bool:
    return (PLUGIN_ROOT / DEFAULT_PLUGIN / "plugin.json").exists() or (
        UPLOAD_ROOT / DEFAULT_PLUGIN / "plugin.json"
    ).exists()


def plugin_dir(plugin_id: str) -> Path:
    if plugin_id == DEFAULT_PLUGIN:
        upload_candidate = UPLOAD_ROOT / plugin_id
        if upload_candidate.exists():
            return upload_candidate
        return PLUGIN_ROOT / plugin_id
    return UPLOAD_ROOT / plugin_id


def _install_target(plugin_id: str) -> Path:
    return UPLOAD_ROOT / plugin_id


def manifest_path(plugin_id: str) -> Path:
    return plugin_dir(plugin_id) / "plugin.json"


def rules_path(plugin_id: str) -> Path:
    return plugin_dir(plugin_id) / "rules.json"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def manifest(plugin_id: str) -> dict:
    if plugin_id in _MANIFEST_CACHE:
        return _MANIFEST_CACHE[plugin_id]
    data = read_json(manifest_path(plugin_id))
    if data:
        _MANIFEST_CACHE[plugin_id] = data
    return data


def list_plugins() -> list[dict]:
    out = []
    seen = set()
    for root in (UPLOAD_ROOT, PLUGIN_ROOT):
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "plugin.json").exists() and child.name not in seen:
                seen.add(child.name)
                item = read_json(child / "plugin.json")
                if item:
                    out.append({
                        "id": child.name,
                        "name": item.get("name") or child.name,
                        "version": item.get("version") or "",
                        "min_core_version": item.get("min_core_version") or "",
                        "description": item.get("description") or "",
                        "author": item.get("author") or "",
                        "active": child.name == active_plugin_id(),
                    })
    return out


def rules(plugin_id: str) -> dict:
    if plugin_id in _RULES_CACHE:
        return _RULES_CACHE[plugin_id]
    data = read_json(rules_path(plugin_id))
    if data:
        _RULES_CACHE[plugin_id] = data
    return data


def active_rules() -> dict:
    plugin_id = active_plugin_id()
    if plugin_id:
        return rules(plugin_id)
    return {}


def builtin_section(section: str, default=None):
    data = active_rules()
    return data.get(section, default if default is not None else {})


def builtin_value(section: str, key: str, default=None):
    data = active_rules()
    section_data = data.get(section) or {}
    return section_data.get(key, default)


def invalidate(plugin_id: str | None = None) -> None:
    if plugin_id:
        _RULES_CACHE.pop(plugin_id, None)
        _MANIFEST_CACHE.pop(plugin_id, None)
    else:
        _RULES_CACHE.clear()
        _MANIFEST_CACHE.clear()


def _validate_manifest(item: dict) -> None:
    plugin_id = str(item.get("id") or "").strip()
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise ValueError("插件 ID 只能是字母、数字、下划线或短横线（2-64 位）")
    if not str(item.get("name") or "").strip():
        raise ValueError("插件缺少 name")
    if not str(item.get("version") or "").strip():
        raise ValueError("插件缺少 version")
    if not str(item.get("min_core_version") or "").strip():
        raise ValueError("插件缺少 min_core_version")
    try:
        required = _version_tuple(str(item.get("min_core_version") or ""))
        current = _version_tuple(APP_VERSION)
    except Exception:
        raise ValueError("插件 min_core_version 格式无效")
    if required > current:
        raise ValueError(f"插件需要 SlowLink >= {item.get('min_core_version')}，当前为 {APP_VERSION}")


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> str:
    names = zf.namelist()
    manifest_rel = None
    rules_rel = None
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized.endswith("/"):
            continue
        parts = [p for p in normalized.split("/") if p not in {"", "."}]
        if not parts or ".." in parts or any(part.startswith("~") for part in parts):
            raise ValueError("插件包包含不安全路径")
        if normalized.endswith("plugin.json"):
            manifest_rel = normalized
        elif normalized.endswith("rules.json"):
            rules_rel = normalized
    if not manifest_rel or not rules_rel:
        raise ValueError("插件包必须包含 plugin.json 和 rules.json")
    prefix_parts = [p for p in manifest_rel.replace("\\", "/").split("/")[:-1] if p not in {"", "."}]
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized.endswith("/") or not normalized:
            continue
        parts = [p for p in normalized.split("/") if p not in {"", "."}]
        if parts[:len(prefix_parts)] != prefix_parts:
            raise ValueError("插件包内文件不在同一插件目录下")

    manifest_raw = zf.read(manifest_rel)
    item = json.loads(manifest_raw.decode("utf-8"))
    _validate_manifest(item)
    plugin_id = str(item.get("id") or "").strip()
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise ValueError("插件 ID 无效")
    if prefix_parts and prefix_parts[-1] != plugin_id:
        raise ValueError("插件目录与 manifest id 不一致")

    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized.endswith("/") or not normalized:
            continue
        parts = [p for p in normalized.split("/") if p not in {"", "."}]
        safe = (
            parts[:len(prefix_parts)] == prefix_parts
            and ".." not in parts
            and not any(part.startswith("~") for part in parts)
        )
        if not safe:
            raise ValueError("插件包包含不安全路径")
        rel = Path(*parts[len(prefix_parts):])
        dest = (target / rel).resolve()
        if not str(dest).startswith(str(target.resolve())):
            raise ValueError("插件包路径越界")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(name) as src, dest.open("wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
    return plugin_id


def install_plugin(raw: bytes) -> dict:
    raw = bytes(raw or b"")
    if not raw:
        raise ValueError("没有收到插件文件")
    if len(raw) > MAX_PLUGIN_BYTES:
        raise ValueError("插件文件超过 10MB 限制")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("插件文件不是有效的 zip")
    try:
        with zf:
            staging = UPLOAD_ROOT / ("_staging_" + str(os.getpid()))
            plugin_id = _safe_extract(zf, staging)
            target = _install_target(plugin_id)
            UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = target.with_name(f"{target.name}.bak-{os.getpid()}")
            if target.exists():
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                shutil.move(str(target), str(backup))
                try:
                    shutil.move(str(staging), str(target))
                except Exception:
                    shutil.move(str(backup), str(target))
                    raise
                shutil.rmtree(backup, ignore_errors=True)
            else:
                shutil.move(str(staging), str(target))
    except Exception:
        staging = UPLOAD_ROOT / ("_staging_" + str(os.getpid()))
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    invalidate()
    return manifest(plugin_id)


def uninstall_plugin(plugin_id: str) -> bool:
    plugin_id = str(plugin_id or "").strip()
    if not plugin_id:
        return False
    target = plugin_dir(plugin_id)
    if not target.exists():
        return False
    if plugin_id != DEFAULT_PLUGIN and not str(target.resolve()).startswith(str(UPLOAD_ROOT.resolve())):
        return False
    shutil.rmtree(target, ignore_errors=True)
    invalidate(plugin_id)
    return True


def activate_plugin(plugin_id: str) -> None:
    plugin_id = str(plugin_id or "").strip()
    if plugin_id and not (plugin_dir(plugin_id) / "plugin.json").exists():
        raise ValueError("插件不存在")
    try:
        from redis_store import set_value
        set_value(ACTIVE_PLUGIN_KEY, plugin_id)
    except Exception:
        pass
    invalidate()


def reload_all() -> None:
    """Re-apply plugin data to core modules after activation changes."""
    from matcher import reload_builtins as reload_matcher_builtins
    from code_rules import reload_builtins as reload_code_builtins
    from dedup import reload_builtins as reload_dedup_builtins

    reload_matcher_builtins()
    reload_code_builtins()
    reload_dedup_builtins()
