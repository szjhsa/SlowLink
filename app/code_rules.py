import re
import time
from typing import Any
import regex as _regex

from redis_store import get_json, set_json
from invite_path_codes import extract_first_invite_path_code, extract_invite_path_codes
from register_code_patterns import HYPHEN_REGISTER_RENEW_PATTERN
from telegram_start_links import (
    extract_first_telegram_start_register_renew_code,
    extract_telegram_start_register_renew_codes,
)

CODE_RULES_KEY = "code_rules"
CODE_RULE_MATCH_TIMEOUT_SECONDS = 0.05

LEGACY_MASKED_REGISTER_SUFFIX_PATTERN = r"(?:[A-Za-z0-9_-]|数字|字母)+"
LEGACY_REGISTER_SUFFIX_BOUNDARY = (
    r"(?=$|\s|[，。！？？；：、）】]"
    r"|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
LEGACY_SYMBOL_REGISTER_SUFFIX_TOKEN_PATTERN = r"(?:[^\s*`\u3400-\u9fff]|数字|字母)"
LEGACY_SYMBOL_REGISTER_SUFFIX_PATTERN = LEGACY_SYMBOL_REGISTER_SUFFIX_TOKEN_PATTERN + "+?"
LEGACY_SYMBOL_REGISTER_SUFFIX_BOUNDARY = (
    r"(?=$|\s|[，。！？？；：、）】,.;:)\]}>`~*](?!"
    + LEGACY_SYMBOL_REGISTER_SUFFIX_TOKEN_PATTERN
    + r"))"
)
# Guess-code suffixes may contain Chinese hints and visible symbols. Spaces and
# Telegram formatting markers still delimit the code candidate.
REGISTER_SUFFIX_TOKEN_PATTERN = r"[^\s*`]"
REGISTER_SUFFIX_PATTERN = REGISTER_SUFFIX_TOKEN_PATTERN + "+?"
OBFUSCATED_REGISTER_SUFFIX_PATTERN = (
    REGISTER_SUFFIX_TOKEN_PATTERN + r"(?:\**" + REGISTER_SUFFIX_TOKEN_PATTERN + r")*?"
)
REGISTER_SUFFIX_BOUNDARY = (
    r"(?=$|\s|[，。！？？；：、）】]"
    r"|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
LEGACY_SAFE_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+"
    r"(?:-[^\s*`-]+)*-(?:Register|Renew)_[A-Za-z0-9_-]+"
)
LEGACY_MASKED_SAFE_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+"
    r"(?:-[^\s*`-]+)*-(?:Register|Renew)_"
    + LEGACY_MASKED_REGISTER_SUFFIX_PATTERN
    + LEGACY_REGISTER_SUFFIX_BOUNDARY
)
LEGACY_SERVER_MASKED_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+"
    r"(?:-[^\s*`-]+)*-(?:Register|Renew)_"
    + LEGACY_MASKED_REGISTER_SUFFIX_PATTERN
    + r"(?![A-Za-z0-9_-]|[\u3400-\u9fff])"
)
SAFE_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+"
    r"(?:-[^\s*`-]+)*-(?:Register|Renew)_" + REGISTER_SUFFIX_PATTERN + REGISTER_SUFFIX_BOUNDARY
)
LEGACY_SAFE_GENERATED_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[A-Za-z0-9]+-\d+"
    r"(?:-[A-Za-z0-9_]+)*-(?:Register|Renew)_[A-Za-z0-9_-]+"
)
LEGACY_MASKED_SAFE_GENERATED_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[A-Za-z0-9]+-\d+"
    r"(?:-[A-Za-z0-9_]+)*-(?:Register|Renew)_"
    + LEGACY_MASKED_REGISTER_SUFFIX_PATTERN
    + LEGACY_REGISTER_SUFFIX_BOUNDARY
)
LEGACY_SYMBOL_SAFE_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+"
    r"(?:-[^\s*`-]+)*-(?:Register|Renew)_"
    + LEGACY_SYMBOL_REGISTER_SUFFIX_PATTERN
    + LEGACY_SYMBOL_REGISTER_SUFFIX_BOUNDARY
)
LEGACY_SYMBOL_SAFE_GENERATED_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[A-Za-z0-9]+-\d+"
    r"(?:-[A-Za-z0-9_]+)*-(?:Register|Renew)_"
    + LEGACY_SYMBOL_REGISTER_SUFFIX_PATTERN
    + LEGACY_SYMBOL_REGISTER_SUFFIX_BOUNDARY
)
SAFE_GENERATED_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[A-Za-z0-9]+-\d+"
    r"(?:-[A-Za-z0-9_]+)*-(?:Register|Renew)_" + REGISTER_SUFFIX_PATTERN + REGISTER_SUFFIX_BOUNDARY
)
WHITELIST_SUFFIX_PATTERN = r"(?a:[A-Za-z0-9]{10})"
WHITELIST_GUESS_SUFFIX_PATTERN = (
    r"(?=[^\s*`]*[\u3400-\u9fff])"
    r"(?=(?:[^A-Za-z0-9\s*`]*[A-Za-z0-9]){10}[^A-Za-z0-9\s*`]*(?=$|\s))"
    + REGISTER_SUFFIX_PATTERN
)
SAFE_WHITELIST_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-Whitelist_"
    + WHITELIST_SUFFIX_PATTERN
    + REGISTER_SUFFIX_BOUNDARY
)
SAFE_GUESS_WHITELIST_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-Whitelist_"
    + WHITELIST_GUESS_SUFFIX_PATTERN
    + REGISTER_SUFFIX_BOUNDARY
)
LEGACY_REGISTER_RENEW_PATTERN_MIGRATIONS = {
    LEGACY_SAFE_REGISTER_RENEW_PATTERN: SAFE_REGISTER_RENEW_PATTERN,
    LEGACY_MASKED_SAFE_REGISTER_RENEW_PATTERN: SAFE_REGISTER_RENEW_PATTERN,
    LEGACY_SERVER_MASKED_REGISTER_RENEW_PATTERN: SAFE_REGISTER_RENEW_PATTERN,
    LEGACY_SAFE_GENERATED_REGISTER_RENEW_PATTERN: SAFE_GENERATED_REGISTER_RENEW_PATTERN,
    LEGACY_MASKED_SAFE_GENERATED_REGISTER_RENEW_PATTERN: SAFE_GENERATED_REGISTER_RENEW_PATTERN,
    LEGACY_SYMBOL_SAFE_REGISTER_RENEW_PATTERN: SAFE_REGISTER_RENEW_PATTERN,
    LEGACY_SYMBOL_SAFE_GENERATED_REGISTER_RENEW_PATTERN: SAFE_GENERATED_REGISTER_RENEW_PATTERN,
    r"[A-Za-z0-9]+-\d+-(?:Register|Renew)_[A-Za-z0-9_-]+": SAFE_GENERATED_REGISTER_RENEW_PATTERN,
    r"[^\s]+-\d+-(?:Register|Renew)_[A-Za-z0-9_-]+": SAFE_REGISTER_RENEW_PATTERN,
    r"[^\s]+(?:-[^\s]+)*-\d+-(?:Register|Renew)_[A-Za-z0-9_-]+": SAFE_REGISTER_RENEW_PATTERN,
    r"(?:^|(?<=[\s:：，,]))[A-Za-z0-9]+-\d+-(?:Register|Renew)_[A-Za-z0-9_-]+": SAFE_GENERATED_REGISTER_RENEW_PATTERN,
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+-(?:Register|Renew)_[A-Za-z0-9_-]+": SAFE_REGISTER_RENEW_PATTERN,
}

# 说明：强格式 Register/Renew 完整码可以直接触发转发。
# 其它码识别默认只负责提取、辅助去重和加速，避免普通验证码、参与码、token 乱发。
DEFAULT_CODE_RULES: list[dict[str, Any]] = [
    {
        "name": "生成类注册码（已为您生成）",
        "pattern": SAFE_GENERATED_REGISTER_RENEW_PATTERN,
        "group": "0",
        "enabled": True,
        "fast": True,
        "trigger": False,
        "strict_context": True,
        "note": "NONAY-30-Register_xxxx / VIP-365-Renew_xxxx；必须有生成/注册上下文",
    },
    {
        "name": "Register/Renew 完整码",
        "pattern": SAFE_REGISTER_RENEW_PATTERN,
        "group": "0",
        "enabled": True,
        "fast": True,
        "trigger": False,
        "strict_context": False,
        "note": "支持多段项目名以及中文提示、@、.、#、% 等猜码内容；完整强格式可直接触发",
    },
    {
        "name": "通用连字符邀请码",
        "pattern": r"\b[A-Z]{2,16}-[A-Z0-9]+(?:-[A-Z0-9]+)+\b",
        "group": "0",
        "enabled": True,
        "fast": True,
        "trigger": False,
        "strict_context": True,
        "note": "INV-xxxx、VIP-xxxx；必须有注册/邀请上下文，只辅助去重",
    },
    {
        "name": "中文字段邀请码/注册码",
        "pattern": r"(?:邀请码|注册码|注册代码)[:：\s]+([A-Za-z0-9_-]+(?:-[A-Za-z0-9_-]+)*)",
        "group": "1",
        "enabled": True,
        "fast": True,
        "trigger": False,
        "strict_context": True,
        "note": "邀请码：xxxx / 注册码：xxxx；不包含参与码/验证码/兑换码",
    },
    {
        "name": "注册链接 code/invite 参数",
        "pattern": r"[?&](?:code|invite|invite_code)=([A-Za-z0-9_-]+(?:-[A-Za-z0-9_-]+)*)",
        "group": "1",
        "enabled": True,
        "fast": True,
        "trigger": False,
        "strict_context": True,
        "note": "register?code=xxxx / invite=xxxx；必须有注册/邀请上下文",
    },
]

_CACHE = {"ts": 0.0, "raw": None, "compiled": []}

POSITIVE_CONTEXT = [
    "邀请注册", "开放注册", "自由注册", "注册开放", "开注", "开注中",
    "注册链接", "邀请链接", "邀请码", "注册码", "注册代码", "注册权益",
    "邀请有效期", "可使用次数", "register", "renew", "register?code", "invite=",
    "emby", "公益服", "server 邀请注册", "server邀请注册",
]

STRONG_POSITIVE_CONTEXT = [
    "邀请注册", "注册链接", "邀请链接", "邀请码", "注册码", "注册代码",
    "邀请有效期", "可使用次数", "注册权益", "register?code", "开放注册", "自由注册", "开注",
]

NEGATIVE_CONTEXT = [
    "已关闭", "关闭", "暂停", "停止", "结束", "已结束",
    "满员", "已满", "满额",
    "登录", "验证码", "校验码", "验证", "二步验证", "绑定", "支付", "订单", "账单",
    "客服", "签到", "口令", "临时", "接口", "api", "authorization", "bearer",
    "access_token", "refresh_token", "token=", "参与码", "活动码", "抽奖口令", "机器人口令",
    "操作 ID", "nmBot", "被删除", "骚扰", "智能识别",
    "tuic", "vless", "vmess", "trojan", "shadowsocks", "hysteria", "wireguard",
    "skip-cert-verify", "congestion-controller", "udp-relay-mode", "alpn",
]

REGISTER_RENEW_RE = re.compile(SAFE_REGISTER_RENEW_PATTERN, re.I | re.M)
HYPHEN_REGISTER_RENEW_RE = re.compile(HYPHEN_REGISTER_RENEW_PATTERN, re.I | re.M)
WHITELIST_RE = re.compile(SAFE_WHITELIST_PATTERN, re.I | re.M)
GUESS_WHITELIST_RE = re.compile(SAFE_GUESS_WHITELIST_PATTERN, re.I | re.M)
MARKDOWN_REGISTER_RENEW_RE = re.compile(
    r"(?:^|[\s:：，,])([^\s*`-]+(?:-[^\s*`-]+)*-\d+(?:-[^\s*`-]+)*-(?:Register|Renew)_)(?:[*`~]+)?("
    + OBFUSCATED_REGISTER_SUFFIX_PATTERN
    + r")"
    + REGISTER_SUFFIX_BOUNDARY,
    re.I | re.M,
)


def _normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    name = str(rule.get("name") or "未命名码规则").strip()[:60]
    pattern = str(rule.get("pattern") or "").strip()
    pattern = LEGACY_REGISTER_RENEW_PATTERN_MIGRATIONS.get(pattern, pattern)
    group = str(rule.get("group") if rule.get("group") is not None else "0").strip() or "0"
    enabled = bool(rule.get("enabled", True))
    fast = bool(rule.get("fast", True))
    # 兼容旧版本：旧库里没有 trigger 字段时，一律默认 False。
    # 这能立刻阻止“码识别直接触发转发”造成的误发。
    trigger = bool(rule.get("trigger", False))
    strict_context = bool(rule.get("strict_context", True))
    note = str(rule.get("note") or "").strip()[:160]
    return {
        "name": name,
        "pattern": pattern,
        "group": group,
        "enabled": enabled,
        "fast": fast,
        "trigger": trigger,
        "strict_context": strict_context,
        "note": note,
    }


def get_code_rules() -> list[dict[str, Any]]:
    try:
        rules = get_json(CODE_RULES_KEY, None)
    except Exception:
        rules = None
    if not rules:
        rules = DEFAULT_CODE_RULES
    out = []
    migrated = False
    for rule in rules:
        if isinstance(rule, dict):
            item = _normalize_rule(rule)
            if item["pattern"] and "8位十六进制" not in item.get("name", ""):
                out.append(item)
                if item["pattern"] != str(rule.get("pattern") or "").strip():
                    migrated = True
    if len(out) < len(rules) or migrated:
        save_code_rules(out)
    return out or list(DEFAULT_CODE_RULES)


def save_code_rules(rules: list[dict[str, Any]]) -> None:
    cleaned = []
    for rule in rules:
        if isinstance(rule, dict):
            item = _normalize_rule(rule)
            if item["pattern"] and "8位十六进制" not in item.get("name", ""):
                cleaned.append(item)
    set_json(CODE_RULES_KEY, cleaned or list(DEFAULT_CODE_RULES))
    _CACHE.update({"ts": 0.0, "raw": None, "compiled": []})


def reset_code_rules() -> None:
    save_code_rules(list(DEFAULT_CODE_RULES))


def add_code_rule(name: str, pattern: str, group: str = "0", fast: bool = True, trigger: bool = False, strict_context: bool = True) -> None:
    rules = get_code_rules()
    item = _normalize_rule({
        "name": name,
        "pattern": pattern,
        "group": group,
        "enabled": True,
        "fast": fast,
        "trigger": trigger,
        "strict_context": strict_context,
    })
    # 保存前先编译，避免坏规则进库。
    _regex.compile(item["pattern"], _regex.I | _regex.M | _regex.S)
    if not any(r.get("pattern") == item["pattern"] and r.get("group") == item["group"] for r in rules):
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
    # 保存前先编译，避免坏规则进库。
    _regex.compile(item["pattern"], _regex.I | _regex.M | _regex.S)
    rules[index] = item
    save_code_rules(rules)
    return True


def _compiled_rules(ttl: float = 30.0):
    now = time.monotonic()
    if _CACHE["compiled"] and now - float(_CACHE["ts"] or 0) <= ttl:
        return _CACHE["compiled"]
    compiled = []
    raw_rules = _CACHE.get("raw") or get_code_rules()
    for idx, rule in enumerate(raw_rules):
        if not rule.get("enabled", True):
            continue
        try:
            compiled.append((idx, rule, _regex.compile(rule["pattern"], _regex.I | _regex.M | _regex.S)))
        except _regex.error:
            continue
    _CACHE.update({"ts": now, "raw": raw_rules, "compiled": compiled})
    return compiled


def _pick_group(match: re.Match, group: str) -> str:
    try:
        if group.isdigit():
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


def _extract_markdown_register_renew_code(text: str) -> str:
    """Extract strong codes and remove literal asterisks used to obscure suffixes."""
    raw = text or ""
    for candidate in [raw, *raw.splitlines()]:
        match = MARKDOWN_REGISTER_RENEW_RE.search(candidate)
        if not match:
            continue
        suffix = re.sub(r"\*+", "", match.group(2) or "")
        code = _clean_code_value((match.group(1) or "") + suffix)
        if REGISTER_RENEW_RE.search(code):
            return code
    return ""


def _extract_hyphen_register_renew_code(text: str) -> str:
    """Extract project-register-segment-segment codes without day/underscore."""
    raw = text or ""
    for candidate in [raw, *raw.splitlines()]:
        match = HYPHEN_REGISTER_RENEW_RE.search(candidate)
        if match:
            return _clean_code_value(match.group(0))
    return ""


def _extract_whitelist_code(text: str) -> str:
    for candidate in [text or "", *(text or "").splitlines()]:
        for pattern in (WHITELIST_RE, GUESS_WHITELIST_RE):
            match = pattern.search(candidate)
            if match:
                return _clean_code_value(match.group(0))
    return ""


def _normalize_text(text: str) -> str:
    return re.sub(r"[\s\u200b\u200c\u200d\ufeff\u2060]+", " ", text or "").strip()


def _has_any(text: str, words: list[str]) -> bool:
    low = (text or "").lower()
    return any(w.lower() in low for w in words)


def _is_safe_code_context(text: str, code: str, rule: dict[str, Any]) -> tuple[bool, str]:
    """判断这次码识别能不能用于完整码去重/极速通道。

    重点：不要让验证码、参与码、接口 token、普通链接 code= 混进来。
    Register/Renew 和固定十位 Whitelist 强格式天然安全；其它宽泛规则必须有注册/邀请上下文。
    """
    raw = _normalize_text(text)
    code = code or ""
    rule_name = str(rule.get("name") or "")
    if (
        any(k in rule_name for k in ("邀请码", "注册码", "注册代码"))
        and re.fullmatch(
            r"(?:\d{1,5}|[xX]\d{1,4})",
            re.sub(r"[^A-Za-z0-9]", "", code) or "",
        )
        and re.search(
            r"(?:邀请码|注册码|注册代码)[:：\s]*"
            + re.escape(re.sub(r"[^A-Za-z0-9]", "", code))
            + r"\s*(?:个|张|组|条|枚|份)(?:\s|$)",
            raw,
        )
    ):
        return False, "疑似注册码数量，不作为完整码去重"
    if code and re.search(re.escape(code) + r"(?:-[^\s-]+)*-(?:Register|Renew)_", raw, re.I):
        return False, "疑似不完整 Register/Renew 前缀"
    if (
        REGISTER_RENEW_RE.search(code)
        or REGISTER_RENEW_RE.search(raw)
        or HYPHEN_REGISTER_RENEW_RE.search(code)
        or HYPHEN_REGISTER_RENEW_RE.search(raw)
    ):
        return True, "Register/Renew 强格式"
    if (
        WHITELIST_RE.search(code)
        or WHITELIST_RE.search(raw)
        or GUESS_WHITELIST_RE.search(code)
        or GUESS_WHITELIST_RE.search(raw)
    ):
        if _has_any(raw, NEGATIVE_CONTEXT):
            return False, "Whitelist 码处于关闭或无效上下文"
        return True, "Whitelist 十位强格式"

    if not rule.get("strict_context", True):
        if _has_any(raw, NEGATIVE_CONTEXT):
            return False, ""
        return True, ""

    has_strong = _has_any(raw, STRONG_POSITIVE_CONTEXT)
    has_positive = has_strong or _has_any(raw, POSITIVE_CONTEXT)
    has_negative = _has_any(raw, NEGATIVE_CONTEXT)
    plain = re.sub(r"[^A-Za-z0-9]", "", code)

    # 抽奖奖品常写成“注册码 x10 / x4 / 10”，这里的 x10 是数量，
    # 不是完整邀请码。否则不同抽奖会被完整码去重误挡。
    if re.fullmatch(r"(?i)(?:x\d{1,4}|\d{1,4})", plain or ""):
        has_lottery_prize_context = _has_any(raw, ["抽奖", "奖品内容", "奖品", "参与关键词", "票开奖", "奖券"])
        if has_lottery_prize_context and _has_any(raw, ["邀请码", "注册码", "注册代码"]):
            return False, "疑似抽奖奖品数量，不作为完整码去重"

    if has_negative and not has_strong:
        return False, "疑似验证码/参与码/token/口令，缺少强注册邀请上下文"
    if not has_positive:
        return False, "缺少注册/邀请上下文"

    # 太短的普通码容易误伤验证码，强上下文才允许。
    if len(plain) < 6 and not has_strong:
        return False, "码太短且缺少强上下文"
    return True, "注册/邀请上下文通过"




def _canonical_code_identity(code: str, rule: dict[str, Any], raw_text: str = "") -> str:
    """生成稳定码身份。

    V1.38：身份不再包含规则名，避免同一个码因命中不同规则而变成不同去重 key。
    """
    value = _clean_code_value(code)
    if not value:
        return ""
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff\u2060]+", "", value)
    rule_name = str(rule.get("name") or "")
    rule_pattern = str(rule.get("pattern") or "")
    is_register_rule = (
        ("Register" in rule_pattern and "Renew" in rule_pattern)
        or "register/renew" in rule_name.lower()
        or rule_pattern == "telegram_bot_start_register_renew"
    )
    is_whitelist_rule = "whitelist" in rule_name.lower() or "whitelist" in rule_pattern.lower()
    whitelist_self = WHITELIST_RE.search(compact) or GUESS_WHITELIST_RE.search(compact)
    whitelist_raw = WHITELIST_RE.search(raw_text or "") or GUESS_WHITELIST_RE.search(raw_text or "")
    if whitelist_self or (is_whitelist_rule and whitelist_raw):
        return "strong_whitelist:" + compact
    strong_self = REGISTER_RENEW_RE.search(compact) or HYPHEN_REGISTER_RENEW_RE.search(compact)
    strong_raw = REGISTER_RENEW_RE.search(raw_text or "") or HYPHEN_REGISTER_RENEW_RE.search(raw_text or "")
    if strong_self or (is_register_rule and strong_raw):
        # Register/Renew 的随机码可能大小写敏感，保留原始大小写，只清理空白。
        return "strong_register_renew:" + compact
    if re.match(r"(?i)^INV-[A-Z0-9]+(?:-[A-Z0-9]+)+$", compact):
        return "invite_code:" + compact.upper()
    name = str(rule.get("name") or "").lower()
    if "链接" in name or "code/invite" in name or "参数" in name:
        return "url_code:" + compact
    if "邀请码" in name or "注册码" in name or "注册代码" in name:
        return "field_code:" + compact
    return "code:" + compact


def normalize_code_identity(identity: str) -> str:
    """Normalize the value part of a code identity for case-insensitive dedup."""
    prefix, sep, value = (identity or "").rpartition(":")
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


def extract_code_detail(text: str, trigger_only: bool = False, safe_only: bool = True) -> dict[str, Any]:
    raw = text or ""
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff\u2060]+", "", raw)
    candidates = [raw, compact] if compact != raw else [raw]
    compiled_rules = _compiled_rules()
    telegram_start_code = extract_first_telegram_start_register_renew_code(raw)
    if telegram_start_code:
        direct_rule = {
            "name": "Telegram start Register/Renew 深链",
            "pattern": "telegram_bot_start_register_renew",
            "fast": True,
            "trigger": False,
            "strict_context": False,
        }
        safe, safe_reason = _is_safe_code_context(raw, telegram_start_code, direct_rule)
        if not safe_only or safe:
            can_trigger = safe and (trigger_only or bool(direct_rule.get("trigger", False)))
            return {
                "index": -1,
                "name": direct_rule["name"],
                "pattern": direct_rule["pattern"],
                "code": telegram_start_code,
                "identity": "strong_register_renew:" + telegram_start_code,
                "fast": True,
                "trigger": bool(trigger_only or direct_rule.get("trigger", False)),
                "can_trigger": can_trigger,
                "strict_context": False,
                "safe": safe,
                "safe_reason": safe_reason,
            }
    invite_path_code = extract_first_invite_path_code(raw)
    if invite_path_code and not trigger_only:
        return {
            "index": -1,
            "name": "网页 /invite/ 六位或八位注册码",
            "pattern": "web_invite_path_code",
            "code": invite_path_code,
            "identity": "url_invite:" + invite_path_code,
            "fast": True,
            "trigger": False,
            "can_trigger": False,
            "strict_context": False,
            "safe": True,
            "safe_reason": "仅辅助去重，需用户正则命中",
        }
    direct_whitelist = _extract_whitelist_code(raw)
    if direct_whitelist:
        # Whitelist is an extraction/dedup format only. The main matcher must
        # hit a configured regex or keyword before this detail can be used.
        if trigger_only:
            return {}
        direct_rule = {
            "name": "Whitelist 完整码",
            "pattern": GUESS_WHITELIST_RE.pattern,
            "fast": True,
            "trigger": False,
            "strict_context": False,
        }
        safe, safe_reason = _is_safe_code_context(raw, direct_whitelist, direct_rule)
        if not safe_only or safe:
            return {
                "index": -1,
                "name": direct_rule["name"],
                "pattern": direct_rule["pattern"],
                "code": direct_whitelist,
                "identity": _canonical_code_identity(direct_whitelist, direct_rule, raw),
                "fast": True,
                "trigger": False,
                "can_trigger": False,
                "strict_context": False,
                "safe": safe,
                "safe_reason": safe_reason,
            }
    direct_code = _extract_markdown_register_renew_code(raw)
    if direct_code:
        direct_idx, direct_rule = 0, {
            "name": "Register/Renew 完整码",
            "pattern": REGISTER_RENEW_RE.pattern,
            "fast": True,
            "trigger": False,
            "strict_context": False,
        }
        for idx, rule, _cre in compiled_rules:
            pattern = str(rule.get("pattern") or "")
            if "Register" in pattern and "Renew" in pattern:
                direct_idx, direct_rule = idx, rule
                break
        safe, safe_reason = _is_safe_code_context(raw, direct_code, direct_rule)
        if not safe_only or safe:
            can_trigger = safe and (trigger_only or bool(direct_rule.get("trigger", False)))
            return {
                "index": direct_idx,
                "name": direct_rule.get("name") or "Register/Renew 完整码",
                "pattern": direct_rule.get("pattern") or REGISTER_RENEW_RE.pattern,
                "code": direct_code,
                "identity": _canonical_code_identity(direct_code, direct_rule, raw),
                "fast": bool(direct_rule.get("fast", True)),
                "trigger": bool(trigger_only or direct_rule.get("trigger", False)),
                "can_trigger": can_trigger,
                "strict_context": bool(direct_rule.get("strict_context", False)),
                "safe": safe,
                "safe_reason": safe_reason,
            }

    hyphen_code = _extract_hyphen_register_renew_code(raw)
    if hyphen_code:
        direct_rule = {
            "name": "Register/Renew 完整码",
            "pattern": HYPHEN_REGISTER_RENEW_RE.pattern,
            "fast": True,
            "trigger": False,
            "strict_context": False,
        }
        safe, safe_reason = _is_safe_code_context(raw, hyphen_code, direct_rule)
        if not safe_only or safe:
            can_trigger = safe and (trigger_only or bool(direct_rule.get("trigger", False)))
            return {
                "index": -1,
                "name": direct_rule.get("name") or "Register/Renew 完整码",
                "pattern": direct_rule.get("pattern") or HYPHEN_REGISTER_RENEW_RE.pattern,
                "code": hyphen_code,
                "identity": _canonical_code_identity(hyphen_code, direct_rule, raw),
                "fast": bool(direct_rule.get("fast", True)),
                "trigger": bool(trigger_only or direct_rule.get("trigger", False)),
                "can_trigger": can_trigger,
                "strict_context": bool(direct_rule.get("strict_context", False)),
                "safe": safe,
                "safe_reason": safe_reason,
            }

    for idx, rule, cre in compiled_rules:
        if trigger_only and not bool(rule.get("trigger", False)):
            continue
        for candidate in candidates:
            try:
                m = cre.search(candidate, timeout=CODE_RULE_MATCH_TIMEOUT_SECONDS)
            except TimeoutError:
                continue
            except Exception:
                continue
            if not m:
                continue
            code = _clean_code_value(_pick_group(m, str(rule.get("group") or "0")))
            if not code:
                continue
            safe, safe_reason = _is_safe_code_context(raw, code, rule)
            if safe_only and not safe:
                continue
            can_trigger = bool(rule.get("trigger", False)) and safe
            return {
                "index": idx,
                "name": rule.get("name") or "码规则",
                "pattern": rule.get("pattern") or "",
                "code": code,
                "identity": _canonical_code_identity(code, rule, raw),
                "fast": bool(rule.get("fast", True)),
                "trigger": bool(rule.get("trigger", False)),
                "can_trigger": can_trigger,
                "strict_context": bool(rule.get("strict_context", True)),
                "safe": safe,
                "safe_reason": safe_reason,
            }
    return {}


def extract_trigger_code_detail(text: str) -> dict[str, Any]:
    return extract_code_detail(text, trigger_only=True, safe_only=True)


def extract_code_identities(text: str) -> list[str]:
    """Return stable identities for every recognized code in one message."""
    raw = text or ""
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff\u2060]+", "", raw)
    candidates = [raw, compact] if compact != raw else [raw]
    identities: list[str] = []
    seen: set[str] = set()

    def push(identity: str) -> None:
        if identity and identity not in seen:
            seen.add(identity)
            identities.append(identity)

    for code in extract_telegram_start_register_renew_codes(raw):
        push("strong_register_renew:" + code)
    for code in extract_invite_path_codes(raw):
        push("url_invite:" + code)

    whitelist_rule = {
        "name": "Whitelist 完整码",
        "pattern": GUESS_WHITELIST_RE.pattern,
        "fast": True,
        "trigger": False,
        "strict_context": False,
    }
    for pattern in (WHITELIST_RE, GUESS_WHITELIST_RE):
        for candidate in candidates:
            for match in pattern.finditer(candidate):
                code = _clean_code_value(match.group(0))
                if not code:
                    continue
                safe, _safe_reason = _is_safe_code_context(raw, code, whitelist_rule)
                if safe:
                    push(_canonical_code_identity(code, whitelist_rule, raw))

    register_rule = {
        "name": "Register/Renew 完整码",
        "pattern": REGISTER_RENEW_RE.pattern,
        "fast": True,
        "trigger": False,
        "strict_context": False,
    }
    for line in raw.splitlines():
        for match in MARKDOWN_REGISTER_RENEW_RE.finditer(line):
            suffix = re.sub(r"\*+", "", match.group(2) or "")
            code = _clean_code_value((match.group(1) or "") + suffix)
            if code and REGISTER_RENEW_RE.search(code):
                push(_canonical_code_identity(code, register_rule, raw))
        for match in HYPHEN_REGISTER_RENEW_RE.finditer(line):
            code = _clean_code_value(match.group(0))
            if code:
                push(_canonical_code_identity(code, register_rule, raw))

    for _idx, rule, cre in _compiled_rules():
        for candidate in candidates:
            try:
                matches = cre.finditer(candidate, timeout=CODE_RULE_MATCH_TIMEOUT_SECONDS)
            except Exception:
                continue
            found = False
            for m in matches:
                found = True
                code = _clean_code_value(_pick_group(m, str(rule.get("group") or "0")))
                if not code:
                    continue
                safe, _safe_reason = _is_safe_code_context(raw, code, rule)
                if safe:
                    push(_canonical_code_identity(code, rule, raw))
            if found:
                break

    return identities


def code_rule_diagnostics() -> list[dict[str, Any]]:
    out = []
    for i, rule in enumerate(get_code_rules()):
        try:
            _regex.compile(rule.get("pattern", ""), _regex.I | _regex.M | _regex.S)
            out.append({"index": i, "ok": True, **rule, "error": ""})
        except _regex.error as e:
            out.append({"index": i, "ok": False, **rule, "error": str(e)})
    return out
