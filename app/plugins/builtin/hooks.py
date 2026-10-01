"""Built-in SlowLink rule generation hooks.

The core only asks the active plugin to generate a pattern. All code and
lottery knowledge stays in this plugin package.
"""

import re

import regex as _regex

from plugin_runtime import load_plugin_module


LOTTERY_ID_LINE_RE = re.compile(
    r"(?m)^[^\n]*(?:抽奖\s*ID|lottery\s*id)\s*[:：]\s*\S+",
    re.I,
)
LOTTERY_SEED_LINE_RE = re.compile(
    r"(?m)^[^\n]*(?:随机种子(?:哈希)?|random\s+seed(?:\s+hash)?)\s*[:：]\s*\S+",
    re.I,
)
LOTTERY_MARKERS = ("抽奖活动已开始", "刮刮乐", "奖品内容", "抽奖信息", "新的抽奖已经创建")
REGISTER_RENEW_CODE_RE = _regex.compile(
    r"(?<![A-Za-z0-9_-])([^\s/?&=#]+(?:-[^\s/?&=#]+)*-\d+-(?:Register|Renew)_[^\s*`]+)",
    re.I,
)
WHITELIST_CODE_RE = _regex.compile(
    r"([^\s*`]+-Whitelist_[^\s*`]+)",
    re.I,
)
INVITE_CODE_RE = _regex.compile(r"\b(INV-[A-Z0-9]+(?:-[A-Z0-9]+)+)\b", re.I)
BARE_CODE_RE = _regex.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{2,8}[A-Za-z0-9]{4,56})(?![A-Za-z0-9])"
)


def _code_impl():
    module = load_plugin_module("code_rules_impl")
    if module is None:
        raise RuntimeError("插件缺少 code_rules_impl")
    return module


def extract_code_detail(payload):
    if not isinstance(payload, dict):
        return {}
    return _code_impl().extract_code_detail(
        str(payload.get("text") or ""),
        trigger_only=bool(payload.get("trigger_only")),
        safe_only=bool(payload.get("safe_only", True)),
        rules=payload.get("rules"),
    )


def extract_code_identities(payload):
    if not isinstance(payload, dict):
        return []
    return _code_impl().extract_code_identities(
        str(payload.get("text") or ""),
        rules=payload.get("rules"),
    )


def normalize_code_identity(payload):
    if not isinstance(payload, dict):
        return ""
    return _code_impl().normalize_code_identity(
        str(payload.get("identity") or "")
    )


def normalize_code_rule(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("rule"), dict):
        return None
    return _code_impl()._normalize_rule(payload.get("rule") or {})


def extract_markdown_code(payload):
    if not isinstance(payload, dict):
        return ""
    return _code_impl()._extract_markdown_register_renew_code(
        str(payload.get("text") or "")
    )


def _dedup_impl():
    module = load_plugin_module("dedup_impl")
    if module is None:
        raise RuntimeError("插件缺少 dedup_impl")
    return module


def build_dedup_profile(payload):
    if not isinstance(payload, dict):
        return None
    profile = _dedup_impl().build_profile(
        str(payload.get("text") or ""),
        str(payload.get("message_link") or ""),
        str(payload.get("source") or ""),
        policy=payload.get("policy") if isinstance(payload.get("policy"), dict) else None,
        code_identities=payload.get("code_identities") or [],
    )
    if not isinstance(profile, dict):
        return profile
    activity = str(profile.get("activity") or "other")
    ttl_keys = {
        "register": "dedup_register_minutes",
        "invite": "dedup_invite_minutes",
        "code": "dedup_code_minutes",
        "lottery": "dedup_lottery_minutes",
        "joint_lottery": "dedup_joint_lottery_minutes",
        "long_term": "dedup_long_term_minutes",
        "other": "dedup_other_minutes",
    }
    profile.setdefault("ttl_key", ttl_keys.get(activity, "dedup_other_minutes"))
    profile.setdefault(
        "correlation_keys",
        [
            value
            for value in (
                profile.get("lottery_template_identity") or "",
                profile.get("lottery_global_identity") or "",
            )
            if value
        ],
    )
    profile.setdefault("strict_identity_conflict", bool(profile.get("lottery_identity")))
    profile.setdefault(
        "identity_conflict_prefix",
        "lottery:" if profile.get("lottery_identity") else "",
    )
    correlation_mode = str(payload.get("correlation_mode") or "")
    if correlation_mode not in {"global", "id", "off"}:
        correlation_mode = "global" if profile.get("correlation_keys") else "off"
    profile["correlation_mode"] = correlation_mode
    profile.setdefault("correlation_key_prefix", "dedup:lottery-template:")
    profile.setdefault("collision_list", "dedup:collisions")
    profile.setdefault(
        "reason_category",
        (
            "identity_fallback"
            if profile.get("identity_fallback")
            else "code"
            if profile.get("dedup_strategy") == "code_identity"
            else "lottery_id"
            if profile.get("lottery_identity")
            else "text"
        ),
    )
    profile.setdefault(
        "reason_labels",
        {
            "no_ttl": "该类型已设置为不去重",
            "identity_fallback": "未识别到完整码，按相同文本内容重复（{ttl}分钟内）",
            "code": "相同完整码重复（{ttl}分钟内）",
            "lottery_id": "相同抽奖 ID 重复（{ttl}分钟内）",
            "text": "相同文本内容重复（{ttl}分钟内）",
            "link": "同一条原消息链接重复（{ttl}分钟内）",
            "template": "同一抽奖的不同模板重复（10分钟内）",
        },
    )
    return profile


def normalize_dedup_text(payload):
    if not isinstance(payload, dict):
        return None
    return _dedup_impl().normalize_for_text_dedup(str(payload.get("text") or ""))


def classify_activity(payload):
    if not isinstance(payload, dict):
        return None
    return _dedup_impl().classify_activity(str(payload.get("text") or ""))


def dedup_ttl_policy(payload):
    if not isinstance(payload, dict):
        return None
    return _dedup_impl().ttl_policy_for_text(str(payload.get("text") or ""))


def register_renew_fingerprints(payload):
    if not isinstance(payload, dict):
        return []
    return _dedup_impl()._register_renew_code_fingerprints(
        str(payload.get("text") or "")
    )


def invite_path_fingerprints(payload):
    if not isinstance(payload, dict):
        return []
    return _dedup_impl()._invite_path_code_fingerprints(
        str(payload.get("text") or "")
    )


def extract_lottery_identity(payload):
    if not isinstance(payload, dict):
        return None
    return _dedup_impl().extract_lottery_identity(str(payload.get("text") or ""))


def extract_lottery_global_identity(payload):
    if not isinstance(payload, dict):
        return None
    return _dedup_impl().extract_lottery_global_identity(
        str(payload.get("text") or "")
    )


def extract_lottery_template_identity(payload):
    if not isinstance(payload, dict):
        return None
    return _dedup_impl().extract_lottery_template_identity(
        str(payload.get("text") or ""),
        str(payload.get("message_link") or ""),
        str(payload.get("source") or ""),
    )


def ttl_minutes_for_profile(payload):
    if not isinstance(payload, dict):
        return None
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return None
    fallback = payload.get("fallback")
    if fallback is not None:
        fallback = int(fallback)
    return _dedup_impl().ttl_minutes_for_profile(profile, fallback)


def _compile_pattern(value, flags=0):
    pattern = str(value or "").strip()
    if not pattern:
        return re.compile(r"(?!)", flags)
    try:
        return re.compile(pattern, flags)
    except re.error:
        return re.compile(r"(?!)", flags)


def _has_any(text: str, words) -> bool:
    low = str(text or "").lower()
    return any(str(word or "").lower() in low for word in words if str(word or ""))


def _usage_notice(payload: dict, normalized: str, compact: str) -> bool:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    code_line_re = _compile_pattern(config.get("code_line_pattern"), re.I)
    inv_code_re = _compile_pattern(config.get("inv_code_pattern"), re.I)
    usage_status_re = _compile_pattern(config.get("usage_status_pattern"), re.I)
    register_patterns = load_plugin_module("register_code_patterns")
    hyphen_pattern = (
        getattr(register_patterns, "HYPHEN_REGISTER_RENEW_PATTERN", r"(?!)")
        if register_patterns is not None
        else r"(?!)"
    )
    hyphen_re = re.compile(hyphen_pattern, re.I | re.M)
    hard_words = list(config.get("usage_hard_words") or [])

    low = normalized.lower()
    compact_low = compact.lower()
    has_status_field = bool(usage_status_re.search(normalized) or usage_status_re.search(compact))
    has_invite_info = (
        "可使用次数" in normalized
        or "邀请有效期" in normalized
        or "注册链接" in normalized
        or "注册权益" in normalized
        or "register?code=" in low
        or "register_" in compact_low
        or "renew_" in compact_low
        or bool(hyphen_re.search(compact))
        or bool(code_line_re.search(compact))
        or bool(inv_code_re.search(compact))
    )
    if has_status_field and has_invite_info:
        return False

    if not _has_any(normalized, hard_words) and not _has_any(compact, hard_words):
        return False

    try:
        from code_rules import extract_code_detail

        code_detail = extract_code_detail(normalized) or extract_code_detail(compact)
    except Exception:
        code_detail = {}
    if code_detail or code_line_re.search(compact) or "register_" in compact_low or "renew_" in compact_low:
        return True
    return any(
        marker in normalized
        for marker in ("邀请码", "注册码", "注册代码", "兑换码", "激活码")
    )


def _closed_register_notice(payload: dict, normalized: str, compact: str) -> bool:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    status_re = _compile_pattern(
        config.get("registration_status_pattern"),
        re.I,
    )
    closed_re = _compile_pattern(config.get("closed_register_pattern"), re.I)
    exhausted_re = _compile_pattern(config.get("exhausted_register_pattern"), re.I)
    open_states = {
        str(value).strip().lower()
        for value in (config.get("open_registration_states") or [])
    }
    closed_states = {
        str(value).strip().lower()
        for value in (config.get("closed_registration_states") or [])
    }
    status_match = status_re.search(normalized)
    if status_match:
        value = str(status_match.group("state") or "").strip().lower()
        if value in closed_states:
            return True
        if value in open_states:
            return False

    if not any(
        marker in normalized or marker in compact
        for marker in ("自由注册", "开放注册", "注册开放", "开注", "注册")
    ):
        return False
    if exhausted_re.search(normalized) or exhausted_re.search(compact):
        return True
    return bool(closed_re.search(normalized) or closed_re.search(compact))


def _registration_success_notice(payload: dict, normalized: str, compact: str) -> bool:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    success_re = _compile_pattern(
        config.get("registration_success_pattern"),
        re.I,
    )
    markers = list(config.get("registration_account_markers") or [])
    if not (success_re.search(normalized) or success_re.search(compact)):
        return False
    return any(marker in normalized or marker in compact for marker in markers)


def analyze_match_guards(payload):
    if not isinstance(payload, dict):
        return None
    normalized = str(payload.get("text") or "")
    compact = str(payload.get("compact") or re.sub(r"\s+", "", normalized))
    return {
        "usage_notice": _usage_notice(payload, normalized, compact),
        "closed_register_notice": _closed_register_notice(payload, normalized, compact),
        "registration_success_notice": _registration_success_notice(
            payload,
            normalized,
            compact,
        ),
    }


def explicit_registration_status(payload):
    if not isinstance(payload, dict):
        return ""
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    status_re = _compile_pattern(config.get("registration_status_pattern"), re.I)
    match = status_re.search(str(payload.get("text") or ""))
    if not match:
        return ""
    value = str(match.group("state") or "").strip().lower()
    if value in {
        str(item).strip().lower()
        for item in (config.get("open_registration_states") or [])
    }:
        return "open"
    if value in {
        str(item).strip().lower()
        for item in (config.get("closed_registration_states") or [])
    }:
        return "closed"
    return ""


def _line_pattern(line: str) -> str:
    parts = [part for part in re.split(r"\s+", line.strip()) if part]
    if not parts:
        raise ValueError("没有可生成的文本")
    return r"(?m)^\s*" + r"\s+".join(re.escape(part) for part in parts) + r"\s*$"


def _looks_like_code_rule(rule: str) -> bool:
    low = str(rule or "").lower()
    if any(word in low for word in ("register", "renew", "whitelist", "invite")):
        return True
    return "[a-z" in low and ("[a-z0-9]" in low or "[a-za-z0-9]" in low)


def _select_existing_rule_pattern(sample: str, candidates) -> str:
    matches = []
    for rule in candidates:
        rule = str(rule or "").strip()
        if not rule or not _looks_like_code_rule(rule):
            continue
        try:
            if _regex.search(rule, sample, timeout=0.05):
                matches.append(rule)
        except (TimeoutError, _regex.error):
            continue
    return min(matches, key=len) if matches else ""


def _matching_existing_regex_pattern(sample: str) -> str:
    try:
        from redis_store import smembers

        rules = set(smembers("regex_rules"))
        disabled = set(smembers("regex_rules_disabled"))
        expanded = []
        for blob in rules:
            for rule in str(blob or "").split(";;"):
                rule = rule.strip()
                if rule and rule not in disabled:
                    expanded.append(rule)
        return _select_existing_rule_pattern(sample, expanded)
    except Exception:
        return ""


def _flexible_pattern_from_existing_rule(rule: str) -> str:
    match = _regex.fullmatch(
        r"^(?:\\b)?([A-Za-z]{2,16})\[[^\]]+\]\{(\d+)\}(?:\\b)?$",
        str(rule or "").strip(),
    )
    if not match:
        return ""
    prefix, length = match.groups()
    return (
        r"(?<![A-Za-z0-9])"
        + re.escape(prefix)
        + r"[^\s]{"
        + str(int(length))
        + r"}"
        + r"(?![A-Za-z0-9])"
    )


def _code_pattern(sample: str) -> tuple[str, str]:
    try:
        from code_rules import extract_code_detail

        detail = extract_code_detail(sample) or {}
        configured_pattern = str(detail.get("pattern") or "").strip()
        if configured_pattern and configured_pattern not in {
            "telegram_bot_start_register_renew",
            "web_invite_path_code",
        }:
            return configured_pattern, "使用现有码识别规则生成通用匹配"
    except Exception:
        pass

    existing_rule_pattern = _matching_existing_regex_pattern(sample)
    if existing_rule_pattern:
        flexible_pattern = _flexible_pattern_from_existing_rule(existing_rule_pattern)
        if flexible_pattern:
            return flexible_pattern, "使用已有规则的固定前缀，后续位置允许中文、星号和符号"
        return existing_rule_pattern, "使用已有规则中的码格式生成通用匹配"

    try:
        match = REGISTER_RENEW_CODE_RE.search(sample, timeout=0.05)
    except TimeoutError:
        raise ValueError("码子识别超时，请缩短样例后重试")
    code = match.group(1).strip() if match else ""
    if not code:
        match = WHITELIST_CODE_RE.search(sample)
        code = match.group(1).strip() if match else ""
    if not code:
        match = INVITE_CODE_RE.search(sample)
        code = match.group(1).strip() if match else ""
    if not code:
        for candidate in BARE_CODE_RE.findall(sample):
            if len(candidate) < 8 or not any(ch.isdigit() for ch in candidate):
                continue
            prefix = candidate[:2]
            tail_length = len(candidate) - len(prefix)
            if tail_length < 4:
                continue
            return (
                r"(?<![A-Za-z0-9])"
                + re.escape(prefix)
                + r"[^\s]"
                + "{"
                + str(tail_length)
                + r"}"
                + r"(?![A-Za-z0-9])",
                "根据固定前缀和后续随机段生成通用码规则，后续允许中文和符号",
            )
        raise ValueError("没有识别到完整码，请检查这条消息")

    for marker in ("Register_", "Renew_"):
        if marker in code:
            base = code.split(marker, 1)[0] + marker
            return (
                re.escape(base) + r"[^\s*`]{6,64}",
                "保留 Register/Renew 固定前缀，只把随机码部分改为可变匹配",
            )

    if "Whitelist_" in code:
        base = code.split("Whitelist_", 1)[0] + "Whitelist_"
        return (
            re.escape(base) + r"[^\s*`]{6,64}",
            "保留 Whitelist 固定前缀，只把随机码部分改为可变匹配",
        )

    return re.escape(code), "未识别到稳定随机段，先生成精确码规则"


def _lottery_pattern(sample: str) -> tuple[str, str]:
    if LOTTERY_ID_LINE_RE.search(sample):
        return (
            r"(?m)^[^\n]*(?:抽奖\s*ID|lottery\s*id)\s*[:：]\s*[A-Za-z0-9_-]{6,128}",
            "使用抽奖 ID 作为稳定触发点",
        )
    if LOTTERY_SEED_LINE_RE.search(sample):
        return (
            r"(?m)^[^\n]*(?:随机种子(?:哈希)?|random\s+seed(?:\s+hash)?)\s*[:：]\s*[A-Fa-f0-9]{16,128}",
            "使用随机种子作为稳定触发点",
        )

    for line in str(sample or "").splitlines():
        stripped = line.strip()
        if stripped and any(marker in stripped for marker in LOTTERY_MARKERS):
            return _line_pattern(stripped), "使用抽奖消息中的稳定标记行"
    raise ValueError("没有识别到抽奖 ID、种子或稳定标记行")


def generate_rule(payload):
    if not isinstance(payload, dict):
        return None
    strategy = str(payload.get("strategy") or "").strip().lower()
    sample = str(payload.get("sample") or "")
    if strategy == "code":
        pattern, reason = _code_pattern(sample)
        return {"pattern": pattern, "reason": reason}
    if strategy == "lottery":
        pattern, reason = _lottery_pattern(sample)
        return {"pattern": pattern, "reason": reason}
    return None
