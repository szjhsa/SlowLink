import time
from typing import Any


CORE_RULE_TYPES = {
    "keyword": {
        "id": "keyword",
        "label": "关键词",
        "strategy": "line",
        "aliases": ["keyword", "关键词", "文本"],
    },
}

ALLOWED_GENERATOR_STRATEGIES = {"code", "lottery", "line"}
CACHE_TTL = 60.0
_CACHE: dict[str, Any] = {"ts": 0.0, "items": []}


def clear_cache() -> None:
    _CACHE.update({"ts": 0.0, "items": []})


def available_rule_types() -> list[dict]:
    now = time.time()
    if now - float(_CACHE.get("ts") or 0.0) <= CACHE_TTL:
        return [dict(item) for item in (_CACHE.get("items") or [])]

    items = [dict(CORE_RULE_TYPES["keyword"])]
    try:
        from plugin_registry import builtin_section

        section = builtin_section("rule_generator", {}) or {}
        plugin_types = section.get("types")
        if isinstance(plugin_types, dict):
            for type_id, raw in plugin_types.items():
                if not isinstance(raw, dict):
                    continue
                normalized_id = str(type_id or "").strip().lower()
                label = str(raw.get("label") or "").strip()
                strategy = str(raw.get("strategy") or "").strip().lower()
                aliases = raw.get("aliases")
                if (
                    not normalized_id
                    or not label
                    or strategy not in ALLOWED_GENERATOR_STRATEGIES
                ):
                    continue
                items.append({
                    "id": normalized_id,
                    "label": label,
                    "strategy": strategy,
                    "aliases": [
                        str(alias).strip().lower()
                        for alias in (aliases if isinstance(aliases, list) else [])
                        if str(alias).strip()
                    ],
                })
    except Exception:
        pass

    _CACHE.update({"ts": now, "items": items})
    return [dict(item) for item in items]


def get_rule_type_config(rule_type: str) -> dict:
    key = str(rule_type or "").strip().lower()
    for item in available_rule_types():
        if key == item["id"] or key in item.get("aliases", []):
            return item
    raise ValueError("当前插件未提供该生成类型")


def is_type_available(rule_type: str) -> bool:
    try:
        get_rule_type_config(rule_type)
        return True
    except ValueError:
        return False
