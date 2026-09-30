import re
from typing import Any

import regex as _regex

from rule_policy import default_policy
from rule_types import available_rule_types, get_rule_type_config


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


def _rule_type_config(rule_type: str) -> dict:
    return get_rule_type_config(rule_type)


def normalize_rule_type(rule_type: str) -> str:
    normalized = _rule_type_config(rule_type)["id"]
    if not normalized:
        raise ValueError("请选择规则类型")
    return normalized


def _first_meaningful_line(sample: str) -> str:
    for raw in str(sample or "").splitlines():
        line = raw.strip()
        if line and not re.fullmatch(r"[━─—=\-]+", line):
            return line
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


def generate_rule(rule_type: str, sample: str) -> dict[str, Any]:
    type_config = _rule_type_config(rule_type)
    normalized_type = type_config["id"]
    strategy = type_config["strategy"]
    raw = str(sample or "")
    if not raw.strip():
        raise ValueError("原消息不能为空")
    if len(raw) > 8192:
        raw = raw[:8192]

    if strategy == "code":
        pattern, reason = _code_pattern(raw)
    elif strategy == "lottery":
        pattern, reason = _lottery_pattern(raw)
    else:
        line = _first_meaningful_line(raw)
        if not line:
            raise ValueError("没有可生成的文本")
        pattern = _line_pattern(line)
        reason = "按首条有效文本生成精确匹配规则"

    _regex.compile(pattern, re.I | re.M)
    policy = default_policy(normalized_type)
    return {
        "pattern": pattern,
        "rule_type": normalized_type,
        "policy": policy,
        "reason": reason,
        "sample": raw[:1200],
    }
