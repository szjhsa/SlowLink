import re
import time
import unicodedata
import regex as _regex
from redis_store import smembers
from code_rules import extract_code_detail
from plugin_registry import builtin_section
from plugin_runtime import call_hook
from rule_policy import default_policy, get_rule_policy, policy_is_available

_RULE_CACHE = {"ts": 0.0, "raw": None, "regexes": []}
_EXCLUDE_TEXT_CACHE = {"ts": 0.0, "raw": None, "items": []}
_SLOW_RULE_LOG: dict[str, float] = {}

# User-supplied regexes run on the listener event loop. A catastrophic rule
# must be bounded so one bad pattern cannot pin a single-core VPS at 100%.
REGEX_MATCH_TIMEOUT_SECONDS = 0.05


def _log_rule_timeout(rule: str) -> None:
    now = time.monotonic()
    key = rule or ""
    if now - _SLOW_RULE_LOG.get(key, 0.0) < 60:
        return
    _SLOW_RULE_LOG[key] = now
    try:
        from redis_store import log_line
        log_line("warning", f"正则匹配超时已跳过（>{int(REGEX_MATCH_TIMEOUT_SECONDS * 1000)}ms）：{(rule or '')[:120]}")
    except Exception:
        pass


def _safe_search(compiled, text: str):
    try:
        return compiled.search(text, timeout=REGEX_MATCH_TIMEOUT_SECONDS)
    except TimeoutError:
        _log_rule_timeout(getattr(compiled, "pattern", "") or "")
        return None
    except Exception:
        return None

def reload_builtins():
    """Compatibility hook; matcher rules are supplied by the active plugin."""
    return None


def _rich_text(node, depth: int = 0) -> str:
    if node is None or depth > 20:
        return ""
    if isinstance(node, str):
        return node

    texts = getattr(node, "texts", None)
    if isinstance(texts, (list, tuple)):
        return "".join(_rich_text(item, depth + 1) for item in texts)

    text = getattr(node, "text", None)
    if isinstance(text, str):
        return text
    if text is not None:
        return _rich_text(text, depth + 1)
    return ""


def _rich_block_text(block, depth: int = 0) -> str:
    if block is None or depth > 20:
        return ""

    text = getattr(block, "text", None)
    if text is not None:
        return _rich_text(text, depth + 1)

    for attr in ("title", "author"):
        value = getattr(block, attr, None)
        if value is not None:
            extracted = _rich_text(value, depth + 1)
            if extracted:
                return extracted

    for attr in ("blocks", "items", "rows", "cells"):
        children = getattr(block, attr, None)
        if isinstance(children, (list, tuple)):
            parts = [_rich_block_text(child, depth + 1) for child in children]
            return "\n".join(part for part in parts if part.strip())
    return ""


def get_text(message) -> str:
    plain = getattr(message, "message", None)
    if plain:
        return plain

    rich_message = getattr(message, "rich_message", None)
    blocks = getattr(rich_message, "blocks", None)
    if not isinstance(blocks, (list, tuple)):
        return ""

    parts = [_rich_block_text(block) for block in blocks]
    return "\n".join(part.strip("\r\n") for part in parts if part.strip())


def normalize_text(text: str) -> str:
    text = text or ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060]", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def compact_text(text: str) -> str:
    text = normalize_text(text)
    return re.sub(r"\s+", "", text)


def _excluded_text_keyword(normalized: str, ttl: float = 60.0) -> str:
    now = time.monotonic()
    cached_raw = _EXCLUDE_TEXT_CACHE.get("raw")
    cached_ts = float(_EXCLUDE_TEXT_CACHE.get("ts") or 0)
    if cached_raw is None or now - cached_ts > ttl:
        raw = tuple(sorted(str(value).strip() for value in smembers("exclude_texts") if str(value).strip()))
        items = []
        for value in raw:
            keyword = normalize_text(value).casefold()
            if keyword:
                items.append((keyword, value))
        _EXCLUDE_TEXT_CACHE.update({"ts": now, "raw": raw, "items": items})
    normalized_folded = normalized.casefold()
    for keyword, original in _EXCLUDE_TEXT_CACHE.get("items") or []:
        if keyword in normalized_folded:
            return original
    return ""


def _split_rule_blob(blob: str) -> list[str]:
    out: list[str] = []
    for part in str(blob or "").split(";;"):
        part = part.strip()
        if part:
            out.append(part)
    return out


def invalidate_rule_cache():
    _RULE_CACHE.clear()
    _RULE_CACHE.update({"ts": 0.0, "raw": None, "regexes": []})
    _EXCLUDE_TEXT_CACHE.update({"ts": 0.0, "raw": None, "items": []})


def _rule_policy_fields(rule: str) -> dict:
    policy = get_rule_policy(rule) or {}
    if policy and not policy_is_available(policy):
        return {
            "rule_type": "",
            "rule_policy": {},
            "rule_policy_unavailable": True,
        }
    return {
        "rule_type": str(policy.get("rule_type") or ""),
        "rule_policy": policy,
        "rule_policy_unavailable": False,
    }


def _compiled_rules(ttl: float = 60.0):
    """Compile each user rule unchanged with timeout-enabled matching."""
    now = time.monotonic()
    cached_raw = _RULE_CACHE.get("raw")
    cached_ts = float(_RULE_CACHE.get("ts") or 0)
    if cached_raw is not None and now - cached_ts <= ttl:
        return _RULE_CACHE
    raw = tuple(sorted(smembers("regex_rules")))
    disabled = set(smembers("regex_rules_disabled"))

    regexes: list[tuple[str, re.Pattern]] = []
    seen = set()

    for blob in raw:
        for rule in _split_rule_blob(blob):
            if rule in seen or rule in disabled:
                continue
            seen.add(rule)

            try:
                cre = _regex.compile(rule, _regex.I | _regex.M)
                regexes.append((rule, cre))
            except _regex.error:
                continue

    _RULE_CACHE.update({"ts": now, "raw": raw, "regexes": regexes})
    return _RULE_CACHE


def _guard_flags(normalized: str, compact: str) -> tuple[bool, bool, bool]:
    plugin_result = call_hook(
        "analyze_match_guards",
        {
            "text": normalized,
            "compact": compact,
            "config": builtin_section("matcher", {}) or {},
        },
        default=None,
    )
    if isinstance(plugin_result, dict):
        return (
            bool(plugin_result.get("usage_notice")),
            bool(plugin_result.get("closed_register_notice")),
            bool(plugin_result.get("registration_success_notice")),
        )
    return (False, False, False)


def _explicit_registration_status(normalized: str) -> str:
    result = call_hook(
        "explicit_registration_status",
        {
            "text": normalized,
            "config": builtin_section("matcher", {}) or {},
        },
        default=None,
    )
    return result if result in {"open", "closed"} else ""


def _is_closed_register_notice(normalized: str, compact: str) -> bool:
    return _guard_flags(normalized, compact)[1]


def _plugin_event_match(original: str, normalized: str, compact: str) -> dict | None:
    """Ask the active plugin whether this is one of its business events."""
    result = call_hook(
        "match_plugin_event",
        {
            "text": original,
            "normalized": normalized,
            "compact": compact,
            "config": builtin_section("matcher", {}) or {},
        },
        default=None,
    )
    if not isinstance(result, dict) or not result.get("matched"):
        return None

    rule = str(result.get("rule") or "plugin:event")
    rule_type = str(result.get("rule_type") or "").strip().lower()
    policy = result.get("policy")
    if not isinstance(policy, dict):
        policy = {}
        if rule_type:
            try:
                policy = default_policy(rule_type)
            except Exception:
                policy = {}
    return {
        "matched": True,
        "rule": rule,
        "rule_type": rule_type,
        "rule_policy": policy,
        "candidate": str(result.get("candidate") or ""),
        "pattern": str(result.get("pattern") or ""),
        "code_detail": result.get("code_detail") if isinstance(result.get("code_detail"), dict) else {},
    }


# ---- main matching (optimized) ----

def analyze_message(text: str) -> dict:
    original = text or ""
    if not original.strip():
        return {
            "matched": False,
            "rule": "",
            "code_detail": {},
            "normalized": "",
            "compact": "",
            "usage_notice": False,
            "closed_register_notice": False,
            "registration_success_notice": False,
        }
    if len(original) > 8192:
        original = original[:8192]

    normalized = normalize_text(original)
    excluded_keyword = _excluded_text_keyword(normalized)
    if excluded_keyword:
        return {
            "matched": False,
            "rule": "",
            "code_detail": {},
            "normalized": normalized,
            "compact": re.sub(r"\s+", "", normalized),
            "excluded_text_notice": True,
            "excluded_keyword": excluded_keyword,
            "usage_notice": False,
            "closed_register_notice": False,
            "registration_success_notice": False,
        }
    compact = re.sub(r"\s+", "", normalized)
    usage_notice, closed_register_notice, registration_success_notice = _guard_flags(
        normalized,
        compact,
    )
    if usage_notice or closed_register_notice or registration_success_notice:
        return {
            "matched": False,
            "rule": "",
            "code_detail": {},
            "normalized": normalized,
            "compact": compact,
            "usage_notice": usage_notice,
            "closed_register_notice": closed_register_notice,
            "registration_success_notice": registration_success_notice,
        }

    rules = _compiled_rules()

    regexes = rules.get("regexes") or []
    for raw, cre in regexes:
        if _safe_search(cre, original):
            plugin_match = _plugin_event_match(original, normalized, compact)
            plugin_code_detail = (
                plugin_match.get("code_detail")
                if isinstance(plugin_match, dict)
                else {}
            )
            code_detail = (
                plugin_code_detail
                or extract_code_detail(normalized)
                or extract_code_detail(compact)
            )
            policy_fields = _rule_policy_fields(raw)
            if (
                plugin_match
                and not policy_fields.get("rule_policy")
            ):
                policy_fields = {
                    "rule_type": plugin_match["rule_type"],
                    "rule_policy": plugin_match["rule_policy"],
                    "rule_policy_unavailable": False,
                }
            return {
                "matched": True,
                "rule": raw,
                "plugin_rule": (
                    plugin_match["rule"] if isinstance(plugin_match, dict) else ""
                ),
                "code_detail": code_detail or {},
                "normalized": normalized,
                "compact": compact,
                "usage_notice": False,
                "closed_register_notice": False,
                **policy_fields,
            }

    return {
        "matched": False,
        "rule": "",
        "code_detail": {},
        "normalized": normalized,
        "compact": compact,
        "usage_notice": False,
        "closed_register_notice": False,
    }


def match_rules(text: str) -> tuple[bool, str]:
    """Run user regexes unchanged against the original message text.

    Guards still use normalized text. Regex input is capped at 8KB to prevent
    pathological backtracking on oversized messages.
    """
    text = text or ""
    if not text.strip():
        return False, ""
    if len(text) > 8192:
        text = text[:8192]

    normalized = normalize_text(text)
    if _excluded_text_keyword(normalized):
        return False, ""
    compact = re.sub(r"\s+", "", normalized)

    # Guards -- pass pre-computed to avoid re-normalization
    usage_notice, closed_register_notice, registration_success_notice = _guard_flags(
        normalized,
        compact,
    )
    if usage_notice:
        return False, ""
    if closed_register_notice:
        return False, ""
    if registration_success_notice:
        return False, ""

    rules = _compiled_rules()

    regexes = rules.get("regexes") or []
    for raw, cre in regexes:
        if _safe_search(cre, text):
            return True, raw

    return False, ""

def expanded_rules() -> list[str]:
    out: list[str] = []
    seen = set()
    for blob in tuple(sorted(smembers("regex_rules"))):
        for rule in _split_rule_blob(blob):
            if rule and rule not in seen:
                seen.add(rule)
                out.append(rule)
    return out


def rule_diagnostics() -> list[dict]:
    items = []
    for rule in expanded_rules():
        try:
            _regex.compile(rule, _regex.I | _regex.M)
            items.append({"rule": rule, "type": "regex", "ok": True, "error": ""})
        except _regex.error as e:
            items.append({"rule": rule, "type": "regex", "ok": False, "error": str(e)})
    return items


def match_rule_details(text: str) -> dict:
    original = text or ""
    if len(original) > 8192:
        original = original[:8192]
    normalized = normalize_text(original)
    excluded_keyword = _excluded_text_keyword(normalized, ttl=0)
    if excluded_keyword:
        return {
            "matched": False, "rule": "", "candidate": "",
            "excluded_text_notice": True, "excluded_keyword": excluded_keyword,
            "usage_notice": False, "closed_register_notice": False, "registration_success_notice": False,
            "code_detected": False, "code_rule": "", "code_note": "",
            "original": original, "normalized": normalized,
            "compact": re.sub(r"\s+", "", normalized),
        }
    compact = re.sub(r"\s+", "", normalized)
    usage, closed_register, registration_success = _guard_flags(normalized, compact)
    code_detail = extract_code_detail(normalized) or extract_code_detail(compact)

    if usage:
        return {
            "matched": False, "rule": "", "candidate": "",
            "usage_notice": True, "closed_register_notice": False, "registration_success_notice": False,
            "code_detected": bool(code_detail),
            "code_rule": code_detail.get("name", "") if code_detail else "",
            "code_note": code_detail.get("safe_reason", "") if code_detail else "",
            "original": original, "normalized": normalized, "compact": compact,
        }
    if closed_register:
        return {
            "matched": False, "rule": "", "candidate": "",
            "usage_notice": False, "closed_register_notice": True, "registration_success_notice": False,
            "code_detected": bool(code_detail),
            "code_rule": code_detail.get("name", "") if code_detail else "",
            "code_note": "已关闭/暂停注册状态，底层安全过滤，不触发转发",
            "original": original, "normalized": normalized, "compact": compact,
        }
    if registration_success:
        return {
            "matched": False, "rule": "", "candidate": "",
            "usage_notice": False, "closed_register_notice": False, "registration_success_notice": True,
            "code_detected": bool(code_detail),
            "code_rule": code_detail.get("name", "") if code_detail else "",
            "code_note": "个人注册成功通知，底层安全过滤，不触发转发",
            "original": original, "normalized": normalized, "compact": compact,
        }

    rules = _compiled_rules(ttl=0)

    regexes = rules.get("regexes") or []
    for raw, cre in regexes:
        if _safe_search(cre, original):
            return {
                "matched": True, "rule": raw, "candidate": "原始文本",
                "usage_notice": False, "closed_register_notice": False,
                "code_detected": bool(code_detail),
                "code_rule": code_detail.get("name", "") if code_detail else "",
                "code_note": code_detail.get("safe_reason", "") if code_detail else "",
                "original": original, "normalized": normalized, "compact": compact,
            }

    return {
        "matched": False, "rule": "", "candidate": "",
        "usage_notice": False, "closed_register_notice": False,
        "code_detected": bool(code_detail),
        "code_rule": code_detail.get("name", "") if code_detail else "",
        "code_note": ("已识别转发身份，但默认仅辅助去重，不触发转发" if code_detail else ""),
        "original": original, "normalized": normalized, "compact": compact,
    }


reload_builtins()
