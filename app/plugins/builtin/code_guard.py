"""Cheap, pure checks for placeholder and fabricated code payloads."""

import re


_FIXED_CODE_WORDS = {"register", "renew", "whitelist", "invite", "ck"}
_PLACEHOLDER_WORDS = {
    "example",
    "examplecode",
    "invitecode",
    "placeholder",
    "replaceme",
    "testtest",
    "yourcode",
}
_SOFT_PLACEHOLDER_WORDS = {"abc123", "testcode"}
_PLACEHOLDER_CONTEXT = (
    "示例",
    "样例",
    "格式",
    "占位",
    "填充",
    "请替换",
    "请填写",
)
_MASK_ONLY_RE = re.compile(r"^(?:[xX]{3,}|[0]{4,}|[*?？._-]{3,})$")
_REPEATED_BLOCK_RE = re.compile(r"^(.{1,4})\1{2,}$", re.IGNORECASE)


def _is_same_character(value: str) -> bool:
    folded = value.casefold()
    return len(folded) >= 4 and len(set(folded)) == 1 and folded.isalnum()


def _is_sequential(value: str) -> bool:
    folded = value.casefold()
    if len(folded) < 4 or not folded.isascii() or not folded.isalnum():
        return False
    values = [ord(ch) for ch in folded]
    steps = {right - left for left, right in zip(values, values[1:])}
    return steps in ({1}, {-1})


def _payload(value: str) -> str:
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff\u2060]+", "", str(value or ""))
    compact = compact.strip("'\"`<({[>) }],，。.!！;；:：")
    if "_" in compact:
        return compact.rsplit("_", 1)[-1]
    if compact.upper().startswith("CK") and len(compact) > 2:
        return compact[2:]
    return compact


def fake_code_reason(value: str, context: str = "") -> str:
    """Return a reason when a value is clearly a placeholder, not a real code."""
    payload = _payload(value)
    if len(payload) < 4:
        return ""
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff\u2060]+", "", payload)
    lowered = compact.casefold()

    if _MASK_ONLY_RE.fullmatch(compact):
        return "疑似占位码"
    if _is_same_character(compact):
        return "疑似全同字符假码"
    if _is_sequential(compact):
        return "疑似连续字符假码"
    if len(compact) >= 6 and _REPEATED_BLOCK_RE.fullmatch(compact):
        return "疑似循环占位码"
    if lowered in _PLACEHOLDER_WORDS:
        return "疑似示例占位码"
    if lowered in _SOFT_PLACEHOLDER_WORDS and any(
        marker in str(context or "")
        for marker in (*_PLACEHOLDER_CONTEXT, "测试")
    ):
        return "疑似示例占位码"
    if any(marker in str(context or "") for marker in _PLACEHOLDER_CONTEXT):
        core = re.sub(r"[^a-z0-9]+", "", lowered)
        if core in _PLACEHOLDER_WORDS | _SOFT_PLACEHOLDER_WORDS or _MASK_ONLY_RE.fullmatch(compact):
            return "示例语境中的占位码"

    tokens = [
        token
        for token in re.split(r"[-_:/]+", compact)
        if len(token) >= 4 and token.casefold() not in _FIXED_CODE_WORDS
    ]
    if len(tokens) >= 2:
        suspicious = sum(
            bool(
                _MASK_ONLY_RE.fullmatch(token)
                or _is_same_character(token)
                or _is_sequential(token)
                or (len(token) >= 6 and _REPEATED_BLOCK_RE.fullmatch(token))
                or token.casefold() in _PLACEHOLDER_WORDS | _SOFT_PLACEHOLDER_WORDS
            )
            for token in tokens
        )
        if suspicious == len(tokens):
            return "疑似全占位码"
    return ""
