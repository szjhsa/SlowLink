import os
import re
import sys
import time
from typing import Any

import regex as _regex

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from redis_store import get_json, set_json
from plugin_registry import active_plugin_id, builtin_section
from plugin_runtime import call_hook


CODE_RULES_KEY = "code_rules"
PURE_CODE_RULES_KEY = "code_rules_pure"
CODE_RULE_MATCH_TIMEOUT_SECONDS = 0.05
DEFAULT_CODE_RULES: list[dict[str, Any]] = []
_CACHE = {"ts": 0.0, "raw": None, "compiled": []}


def _generic_normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    name = str(rule.get("name") or "未命名规则").strip()[:60]
    pattern = str(rule.get("pattern") or "").strip()
    group = str(rule.get("group") if rule.get("group") is not None else "0").strip() or "0"
    return {
        "name": name,
        "pattern": pattern,
        "group": group,
        "enabled": bool(rule.get("enabled", True)),
        "fast": bool(rule.get("fast", True)),
        "trigger": bool(rule.get("trigger", False)),
        "strict_context": bool(rule.get("strict_context", True)),
        "note": str(rule.get("note") or "").strip()[:160],
    }


def _normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    item = _generic_normalize_rule(rule)
    result = call_hook("normalize_code_rule", {"rule": item}, default=None)
    if isinstance(result, dict):
        item.update(result)
        item = _generic_normalize_rule(item)
    return item


def reload_builtins() -> None:
    global DEFAULT_CODE_RULES
    _CACHE.update({"ts": 0.0, "raw": None, "compiled": []})
    section = builtin_section("code_rules", {}) or {}
    defaults = section.get("default_rules")
    if isinstance(defaults, list):
        DEFAULT_CODE_RULES = [
            _normalize_rule(rule)
            for rule in defaults
            if isinstance(rule, dict) and str(rule.get("pattern") or "").strip()
        ]
    else:
        DEFAULT_CODE_RULES = []


def get_code_rules() -> list[dict[str, Any]]:
    return get_code_rules_for_source("plugin" if active_plugin_id() else "pure")


def get_code_rules_for_source(source: str) -> list[dict[str, Any]]:
    key = CODE_RULES_KEY if source == "plugin" else PURE_CODE_RULES_KEY
    try:
        raw_rules = get_json(key, None)
    except Exception:
        raw_rules = None
    if not raw_rules:
        return list(DEFAULT_CODE_RULES) if source == "plugin" else []

    out: list[dict[str, Any]] = []
    migrated = False
    for raw in raw_rules:
        if not isinstance(raw, dict):
            migrated = True
            continue
        item = _normalize_rule(raw)
        if not item["pattern"]:
            migrated = True
            continue
        out.append(item)
        if item != _generic_normalize_rule(raw):
            migrated = True
    if migrated or len(out) != len(raw_rules):
        save_code_rules_to_source(out, source)
    return out or (list(DEFAULT_CODE_RULES) if source == "plugin" else [])


def save_code_rules(rules: list[dict[str, Any]]) -> None:
    save_code_rules_to_source(rules, "plugin" if active_plugin_id() else "pure")


def save_code_rules_to_source(rules: list[dict[str, Any]], source: str) -> None:
    cleaned = []
    for rule in rules:
        if isinstance(rule, dict):
            item = _normalize_rule(rule)
            if item["pattern"]:
                cleaned.append(item)
    key = CODE_RULES_KEY if source == "plugin" else PURE_CODE_RULES_KEY
    set_json(key, cleaned or (list(DEFAULT_CODE_RULES) if source == "plugin" else []))
    _CACHE.update({"ts": 0.0, "raw": None, "compiled": []})


def reset_code_rules() -> None:
    save_code_rules(list(DEFAULT_CODE_RULES) if active_plugin_id() else [])


def add_code_rule(
    name: str,
    pattern: str,
    group: str = "0",
    fast: bool = True,
    trigger: bool = False,
    strict_context: bool = True,
) -> None:
    item = _normalize_rule({
        "name": name,
        "pattern": pattern,
        "group": group,
        "enabled": True,
        "fast": fast,
        "trigger": trigger,
        "strict_context": strict_context,
    })
    _regex.compile(item["pattern"], _regex.I | _regex.M | _regex.S)
    rules = get_code_rules()
    if not any(
        existing.get("pattern") == item["pattern"]
        and existing.get("group") == item["group"]
        for existing in rules
    ):
        rules.append(item)
    save_code_rules(rules)


def delete_code_rule(index: int) -> bool:
    rules = get_code_rules()
    if 0 <= index < len(rules):
        rules.pop(index)
        save_code_rules(rules)
        return True
    return False


def update_code_rule(index: int, patch: dict[str, Any]) -> bool:
    rules = get_code_rules()
    if not (0 <= index < len(rules)):
        return False
    current = dict(rules[index])
    current.update(patch or {})
    item = _normalize_rule(current)
    _regex.compile(item["pattern"], _regex.I | _regex.M | _regex.S)
    rules[index] = item
    save_code_rules(rules)
    return True


def _rules_for_hook() -> list[dict[str, Any]]:
    now = time.monotonic()
    raw_rules = _CACHE.get("raw")
    if raw_rules is not None and now - float(_CACHE.get("ts") or 0.0) <= 30.0:
        return raw_rules
    raw_rules = get_code_rules()
    _CACHE.update({"ts": now, "raw": raw_rules, "compiled": []})
    return raw_rules


def _compiled_rules(
    ttl: float = 30.0,
    rules: list[dict[str, Any]] | None = None,
):
    now = time.monotonic()
    if rules is None and _CACHE["compiled"] and now - float(_CACHE["ts"] or 0) <= ttl:
        return _CACHE["compiled"]
    raw_rules = rules if rules is not None else _rules_for_hook()
    compiled = []
    for index, rule in enumerate(raw_rules):
        if not rule.get("enabled", True):
            continue
        try:
            compiled.append((
                index,
                rule,
                _regex.compile(rule["pattern"], _regex.I | _regex.M | _regex.S),
            ))
        except _regex.error:
            continue
    if rules is None:
        _CACHE.update({"ts": now, "raw": raw_rules, "compiled": compiled})
    return compiled


def _pick_group(match, group: str) -> str:
    try:
        if str(group).isdigit():
            return match.group(int(group)) or ""
        return match.group(group) or ""
    except Exception:
        try:
            return match.group(0) or ""
        except Exception:
            return ""


def _clean_code_value(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"^[\s'\"`<({\[]+|[\s'\"`>)}\],，。.!！?？;；:：]+$", "", value)
    return value.strip()


def _strong_codes_enabled() -> bool:
    section = builtin_section("code_rules", {}) or {}
    return bool(section.get("enable_strong_codes", False))


def _extract_markdown_register_renew_code(text: str) -> str:
    result = call_hook("extract_markdown_code", {"text": text}, default=None)
    return result if isinstance(result, str) else ""


def normalize_code_identity(identity: str) -> str:
    if identity:
        result = call_hook(
            "normalize_code_identity",
            {"identity": identity},
            default=None,
        )
        if isinstance(result, str):
            return result
    prefix, sep, value = (identity or "").partition(":")
    if sep and prefix in {
        "strong_register_renew",
        "strong_whitelist",
        "url_invite",
        "url_code",
        "field_code",
        "code",
        "invite_code",
    }:
        return prefix + ":" + value.lower()
    return identity or ""


def extract_code_detail(
    text: str,
    trigger_only: bool = False,
    safe_only: bool = True,
) -> dict[str, Any]:
    raw = text or ""
    rules = _rules_for_hook()
    result = call_hook(
        "extract_code_detail",
        {
            "text": raw,
            "trigger_only": trigger_only,
            "safe_only": safe_only,
            "rules": rules,
        },
        default=None,
    )
    if isinstance(result, dict):
        return result

    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff\u2060]+", "", raw)
    candidates = [raw, compact] if compact != raw else [raw]
    for index, rule, compiled in _compiled_rules(rules=rules):
        if trigger_only and not bool(rule.get("trigger", False)):
            continue
        for candidate in candidates:
            try:
                match = compiled.search(candidate, timeout=CODE_RULE_MATCH_TIMEOUT_SECONDS)
            except Exception:
                continue
            if not match:
                continue
            code = _clean_code_value(_pick_group(match, str(rule.get("group") or "0")))
            if not code:
                continue
            return {
                "index": index,
                "name": rule.get("name") or "自定义规则",
                "pattern": rule.get("pattern") or "",
                "code": code,
                "identity": "code:" + code,
                "fast": bool(rule.get("fast", True)),
                "trigger": bool(rule.get("trigger", False)),
                "can_trigger": bool(rule.get("trigger", False)),
                "strict_context": bool(rule.get("strict_context", True)),
                "safe": True,
                "safe_reason": "纯净自定义规则",
            }
    return {}


def extract_trigger_code_detail(text: str) -> dict[str, Any]:
    return extract_code_detail(text, trigger_only=True, safe_only=True)


def extract_code_identities(text: str) -> list[str]:
    raw = text or ""
    rules = _rules_for_hook()
    result = call_hook(
        "extract_code_identities",
        {"text": raw, "rules": rules},
        default=None,
    )
    if isinstance(result, list):
        return [str(value) for value in result if str(value)]

    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff\u2060]+", "", raw)
    candidates = [raw, compact] if compact != raw else [raw]
    identities: list[str] = []
    seen: set[str] = set()
    for _index, rule, compiled in _compiled_rules(rules=rules):
        for candidate in candidates:
            try:
                matches = compiled.finditer(candidate, timeout=CODE_RULE_MATCH_TIMEOUT_SECONDS)
            except Exception:
                continue
            for match in matches:
                code = _clean_code_value(_pick_group(match, str(rule.get("group") or "0")))
                if not code:
                    continue
                identity = "code:" + code
                if identity not in seen:
                    seen.add(identity)
                    identities.append(identity)
    return identities


def code_rule_diagnostics() -> list[dict[str, Any]]:
    out = []
    for index, rule in enumerate(get_code_rules()):
        try:
            _regex.compile(rule.get("pattern", ""), _regex.I | _regex.M | _regex.S)
            out.append({"index": index, "ok": True, **rule, "error": ""})
        except _regex.error as exc:
            out.append({"index": index, "ok": False, **rule, "error": str(exc)})
    return out


reload_builtins()
