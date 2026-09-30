import re
from typing import Any

import regex as _regex

from rule_policy import default_policy


RULE_TYPE_ALIASES = {
    "code": "code",
    "码子": "code",
    "马子": "code",
    "注册码": "code",
    "lottery": "lottery",
    "抽奖": "lottery",
    "刮刮乐": "lottery",
    "keyword": "keyword",
    "关键词": "keyword",
    "文本": "keyword",
    "exclude": "exclude",
    "排除": "exclude",
    "排除文本": "exclude",
}

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


def normalize_rule_type(rule_type: str) -> str:
    key = str(rule_type or "").strip().lower()
    normalized = RULE_TYPE_ALIASES.get(key)
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


def _code_pattern(sample: str) -> tuple[str, str]:
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
    normalized_type = normalize_rule_type(rule_type)
    raw = str(sample or "")
    if not raw.strip():
        raise ValueError("原消息不能为空")
    if len(raw) > 8192:
        raw = raw[:8192]

    if normalized_type == "code":
        pattern, reason = _code_pattern(raw)
    elif normalized_type == "lottery":
        pattern, reason = _lottery_pattern(raw)
    else:
        line = _first_meaningful_line(raw)
        if not line:
            raise ValueError("没有可生成的文本")
        pattern = _line_pattern(line)
        reason = "按首条有效文本生成精确匹配规则"

    re.compile(pattern, re.I | re.M)
    policy = default_policy(normalized_type)
    return {
        "pattern": pattern,
        "rule_type": normalized_type,
        "policy": policy,
        "reason": reason,
        "sample": raw[:1200],
    }
