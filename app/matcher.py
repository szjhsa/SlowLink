import re
import time
import unicodedata
import regex as _regex
from redis_store import smembers
from code_rules import extract_code_detail, extract_trigger_code_detail
from register_code_patterns import HYPHEN_REGISTER_RENEW_PATTERN

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

# ---- pre-compiled guards (unchanged) ----

USAGE_HARD_WORDS = [
    "码使用", "注册码使用", "邀请码使用", "注册代码使用",
    "被使用", "已被使用", "已经使用", "使用成功",
    "使用了", "兑换成功", "已兑换", "被兑换", "激活成功",
    "领取成功", "已领取", "被领取", "使用者", "使用用户",
    "使用注册码", "使用邀请码", "成功注册账号", "成功注册",
]

CODE_LINE_RE = re.compile(
    r"^.+-\d+-(?:Register|Renew)_[^\s*`]+$",
    re.I,
)
HYPHEN_REGISTER_RENEW_RE = re.compile(HYPHEN_REGISTER_RENEW_PATTERN, re.I | re.M)
INV_CODE_RE = re.compile(r"\bINV-[A-Z0-9]+(?:-[A-Z0-9]+)+\b", re.I)
USAGE_STATUS_RE = re.compile(r"已使用\s*[:：]\s*\d+\s*次?", re.I)

CLOSED_REGISTER_RE = re.compile(
    r"(?:"
    r"(?:已关闭|关闭|暂停|停止|结束|已结束|暂不开放|不开放|未开放)\s*(?:自由注册|开放注册|注册开放|开注|注册)"
    r"|(?:自由注册|开放注册|注册开放|开注)\s*(?:已关闭|关闭|暂停|停止|结束|已结束)"
    r"|注册\s*(?:已关闭|关闭|暂停|停止|结束|已结束|暂不开放|不开放|未开放)"
    r"|(?:满员|已满|满额)"
    r")",
    re.I,
)
REGISTRATION_STATUS_RE = re.compile(
    r"(?m)^[^\n]*?(?:注册|开注)状态\s*[|｜:：]\s*"
    r"(?P<state>true|false|on|off|enabled|disabled|1|0|已开启|开启|开放|已关闭|关闭|未开放)"
    r"(?=$|\s|[•·])",
    re.I,
)
EXHAUSTED_REGISTER_RE = re.compile(
    r"(?:剩余可注册(?:人数)?|剩余名额|可注册名额)\s*[|｜:：]\s*"
    r"(?:\*\*)?\s*0\s*(?:\*\*)?",
    re.I,
)
OPEN_REGISTRATION_STATES = {"true", "on", "enabled", "1", "已开启", "开启", "开放"}
CLOSED_REGISTRATION_STATES = {"false", "off", "disabled", "0", "已关闭", "关闭", "未开放"}
REGISTRATION_SUCCESS_RE = re.compile(
    r"(?:自由|定时|开放)?注册成功(?=$|\s|[-—:：|，。!！])",
    re.I,
)
REGISTRATION_ACCOUNT_MARKERS = ["创建了", "账号有效期", "到期时间"]

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


def _compiled_rules(ttl: float = 60.0):
    """Compile each user rule unchanged with timeout-enabled matching."""
    now = time.monotonic()
    cached_raw = _RULE_CACHE.get("raw")
    cached_ts = float(_RULE_CACHE.get("ts") or 0)
    if cached_raw is not None and now - cached_ts <= ttl:
        return _RULE_CACHE
    raw = tuple(sorted(smembers("regex_rules")))

    regexes: list[tuple[str, re.Pattern]] = []
    seen = set()

    for blob in raw:
        for rule in _split_rule_blob(blob):
            if rule in seen:
                continue
            seen.add(rule)

            try:
                cre = _regex.compile(rule, _regex.I | _regex.M)
                regexes.append((rule, cre))
            except _regex.error:
                continue

    _RULE_CACHE.update({"ts": now, "raw": raw, "regexes": regexes})
    return _RULE_CACHE


# ---- usage / closed-register guards (unchanged logic) ----

def _is_usage_notice(normalized: str, compact: str) -> bool:
    low = normalized.lower()
    compact_low = compact.lower()

    has_status_field = bool(USAGE_STATUS_RE.search(normalized) or USAGE_STATUS_RE.search(compact))
    has_invite_info = (
        "可使用次数" in normalized
        or "邀请有效期" in normalized
        or "注册链接" in normalized
        or "注册权益" in normalized
        or "register?code=" in low
        or "register_" in compact_low
        or "renew_" in compact_low
        or bool(HYPHEN_REGISTER_RENEW_RE.search(compact))
        or bool(CODE_LINE_RE.search(compact))
        or bool(INV_CODE_RE.search(compact))
    )
    if has_status_field and has_invite_info:
        return False

    has_hard_usage = any(w.lower() in low or w.lower() in compact_low for w in USAGE_HARD_WORDS)
    if not has_hard_usage:
        return False

    code_detail = extract_code_detail(normalized) or extract_code_detail(compact)
    if code_detail or CODE_LINE_RE.search(compact) or "register_" in compact_low or "renew_" in compact_low:
        return True

    if any(k in normalized for k in ["邀请码", "注册码", "注册代码", "兑换码", "激活码"]):
        return True

    return False


def _explicit_registration_status(normalized: str) -> str:
    match = REGISTRATION_STATUS_RE.search(normalized)
    if not match:
        return ""
    value = match.group("state").strip().lower()
    if value in OPEN_REGISTRATION_STATES:
        return "open"
    if value in CLOSED_REGISTRATION_STATES:
        return "closed"
    return ""


def _is_closed_register_notice(normalized: str, compact: str) -> bool:
    if _explicit_registration_status(normalized) == "closed":
        return True

    has_register_marker = any(k in normalized or k in compact for k in [
        "自由注册", "开放注册", "注册开放", "开注", "注册"
    ])
    if not has_register_marker:
        return False

    if EXHAUSTED_REGISTER_RE.search(normalized) or EXHAUSTED_REGISTER_RE.search(compact):
        return True

    return bool(CLOSED_REGISTER_RE.search(normalized) or CLOSED_REGISTER_RE.search(compact))


def _is_registration_success_notice(normalized: str, compact: str) -> bool:
    has_success = bool(
        REGISTRATION_SUCCESS_RE.search(normalized)
        or REGISTRATION_SUCCESS_RE.search(compact)
    )
    if not has_success:
        return False
    return any(marker in normalized or marker in compact for marker in REGISTRATION_ACCOUNT_MARKERS)


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
    usage_notice = _is_usage_notice(normalized, compact)
    closed_register_notice = _is_closed_register_notice(normalized, compact)
    registration_success_notice = _is_registration_success_notice(normalized, compact)
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
            code_detail = extract_code_detail(normalized) or extract_code_detail(compact)
            return {
                "matched": True,
                "rule": raw,
                "code_detail": code_detail or {},
                "normalized": normalized,
                "compact": compact,
                "usage_notice": False,
                "closed_register_notice": False,
            }

    trigger_detail = extract_trigger_code_detail(normalized) or extract_trigger_code_detail(compact)
    if trigger_detail and trigger_detail.get("can_trigger"):
        return {
            "matched": True,
            "rule": "code_trigger:" + str(trigger_detail.get("name") or "full_code"),
            "code_detail": trigger_detail,
            "normalized": normalized,
            "compact": compact,
            "usage_notice": False,
            "closed_register_notice": False,
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
    if _is_usage_notice(normalized, compact):
        return False, ""
    if _is_closed_register_notice(normalized, compact):
        return False, ""
    if _is_registration_success_notice(normalized, compact):
        return False, ""

    rules = _compiled_rules()

    regexes = rules.get("regexes") or []
    for raw, cre in regexes:
        if _safe_search(cre, text):
            return True, raw

    # Code-trigger fallback
    code_detail = extract_trigger_code_detail(normalized) or extract_trigger_code_detail(compact)
    if code_detail and code_detail.get("can_trigger"):
        return True, "码识别触发：" + str(code_detail.get("name") or "完整码")

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
    usage = _is_usage_notice(normalized, compact)
    closed_register = _is_closed_register_notice(normalized, compact)
    registration_success = _is_registration_success_notice(normalized, compact)
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

    # Code trigger fallback
    trigger_detail = extract_trigger_code_detail(normalized) or extract_trigger_code_detail(compact)
    if trigger_detail and trigger_detail.get("can_trigger"):
        return {
            "matched": True, "rule": "码识别触发：" + str(trigger_detail.get("name") or "完整码"),
            "candidate": "码识别规则",
            "usage_notice": False, "closed_register_notice": False,
            "code_detected": True,
            "code_rule": trigger_detail.get("name", ""),
            "code_note": trigger_detail.get("safe_reason", ""),
            "original": original, "normalized": normalized, "compact": compact,
        }

    return {
        "matched": False, "rule": "", "candidate": "",
        "usage_notice": False, "closed_register_notice": False,
        "code_detected": bool(code_detail),
        "code_rule": code_detail.get("name", "") if code_detail else "",
        "code_note": ("已识别完整码，但默认仅辅助去重，不触发转发" if code_detail else ""),
        "original": original, "normalized": normalized, "compact": compact,
    }
