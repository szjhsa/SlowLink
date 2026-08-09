import hashlib
import json
import re
import time
import unicodedata
from typing import Any

from invite_path_codes import extract_invite_path_codes
from redis_store import r, sha, format_time
from telegram_start_links import extract_telegram_start_register_renew_codes

try:
    from redis_store import add_lottery_collision as _add_lottery_collision
    from redis_store import is_collision_exempt as _is_collision_exempt
except (ImportError, AttributeError):
    def _add_lottery_collision(item: dict) -> None:
        try:
            import json as _json
            r.lpush("dedup:collisions", _json.dumps(item, ensure_ascii=False))
            r.ltrim("dedup:collisions", 0, 199)
        except Exception:
            pass

    def _is_collision_exempt(identity: str, dedup_id: str) -> bool:
        try:
            return bool(r.sismember("dedup:collision_exempt:" + sha(identity), str(dedup_id)))
        except Exception:
            return False

# ---- Text normalization for dedup ----

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)

RECENT_LIST = "dedup:recent"
META_PREFIX = "dedup:meta:"

# Module-level TTL cache to avoid per-message Redis GET
_TTL_CACHE: dict[str, tuple[float, int]] = {}
_TTL_CACHE_TTL = 30.0
_LOTTERY_TEMPLATE_MODE_CACHE: dict[str, object] = {"ts": 0.0, "value": "global"}
LOTTERY_TEMPLATE_MODE_KEY = "dedup_lottery_template_mode"


def clear_ttl_cache():
    _TTL_CACHE.clear()
    _LOTTERY_TEMPLATE_MODE_CACHE.update({"ts": 0.0, "value": "global"})


def _now() -> int:
    return int(time.time())


def _ts() -> str:
    return format_time()


def _meta_key(dedup_id: str) -> str:
    return META_PREFIX + sha(dedup_id)


def _load_meta(dedup_id: str) -> dict[str, Any]:
    raw = r.get(_meta_key(dedup_id))
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_meta(meta: dict[str, Any], ttl_seconds: int) -> None:
    r.setex(_meta_key(meta["dedup_id"]), ttl_seconds, json.dumps(meta, ensure_ascii=False))


def _push_recent(item: dict[str, Any], limit: int = 120) -> None:
    item = dict(item)
    item.setdefault("time", _ts())
    pipe = r.pipeline()
    pipe.lpush(RECENT_LIST, json.dumps(item, ensure_ascii=False))
    pipe.ltrim(RECENT_LIST, 0, limit - 1)
    pipe.execute()


# ---- Activity classification ----

REGISTER_KWS = ["开放注册", "自由注册", "开放注册中", "注册开放", "开注", "开注中", "开放名额", "register", "renew"]
LOTTERY_KWS = ["抽奖", "抽奖开始", "抽奖开始啦", "抽奖活动已开始", "刮刮乐", "奖品内容", "抽奖信息", "新的抽奖已经创建"]
JOINT_LOTTERY_KWS = ["联合抽奖", "发起了通用抽奖活动", "通用抽奖", "多个群", "多个频道", "共同抽奖"]
LONG_TERM_KWS = ["长期活动", "持续多日", "长期抽奖", "延期", "延迟开奖", "开奖延期", "活动延期", "持续到", "截止", "截至"]
INVITE_KWS = ["邀请码", "注册码", "注册代码", "邀请链接", "注册链接", "register?code", "INV-", "已为您生成", "生成了", "兑换码", "激活码"]

LOTTERY_ID_RE = re.compile(
    r"(?:抽奖\s*ID|lottery\s*id)\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9_-]{5,127})",
    re.I,
)
LOTTERY_SEED_RE = re.compile(
    r"(?:随机种子(?:哈希)?|random\s+seed(?:\s+hash)?)\s*[:：]\s*([a-f0-9]{16,128})",
    re.I,
)
LOTTERY_TEMPLATE_WINDOW_SECONDS = 10 * 60


def _lower(text: str) -> str:
    return (text or "").lower()


def classify_activity(text: str) -> str:
    low = _lower(text)
    if any(kw.lower() in low for kw in JOINT_LOTTERY_KWS):
        return "joint_lottery"
    if any(kw.lower() in low for kw in LOTTERY_KWS) or "参与关键词" in text or "抽奖 id" in low or "抽奖id" in low:
        return "lottery"
    if any(kw.lower() in low for kw in INVITE_KWS):
        return "invite"
    if any(kw.lower() in low for kw in REGISTER_KWS):
        return "register"
    if any(kw.lower() in low for kw in LONG_TERM_KWS):
        return "long_term"
    return "other"


def ttl_policy_for_text(text: str) -> str:
    low = _lower(text)
    return "long_term" if any(kw.lower() in low for kw in LONG_TERM_KWS) else "normal"


def lottery_template_dedup_mode() -> str:
    now = time.time()
    cached_ts = float(_LOTTERY_TEMPLATE_MODE_CACHE.get("ts") or 0)
    if now - cached_ts <= _TTL_CACHE_TTL:
        return str(_LOTTERY_TEMPLATE_MODE_CACHE.get("value") or "global")
    try:
        value = str(r.get(LOTTERY_TEMPLATE_MODE_KEY) or "global")
    except Exception:
        value = "global"
    if value not in {"global", "id", "off"}:
        value = "global"
    _LOTTERY_TEMPLATE_MODE_CACHE.update({"ts": now, "value": value})
    return value


def extract_lottery_identity(text: str) -> str:
    """Return a stable identity shared by alternate templates for one lottery."""
    raw = unicodedata.normalize("NFKC", text or "")
    raw = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060]", "", raw)
    match = LOTTERY_ID_RE.search(raw)
    if match:
        return "id:" + match.group(1).lower()
    match = LOTTERY_SEED_RE.search(raw)
    if match:
        return "seed:" + match.group(1).lower()
    return ""


def _normalize_lottery_identity_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").lower().replace("×", "x")
    normalized = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060]", "", normalized)
    normalized = EMOJI_RE.sub("", normalized)
    normalized = re.sub(r"[`*_~\s]+", "", normalized)
    normalized = re.sub(r"[\[\]【】（）(){}<>《》|:：;；,，。!！?？/\\]", "", normalized)
    return normalized.strip()


def _extract_lottery_line_value(raw: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    for line in raw.splitlines():
        candidate = re.sub(r"^[^\w\u3400-\u9fff]+", "", line.strip())
        match = re.match(rf"(?:{label_pattern})\s*[:：]\s*(.+?)\s*$", candidate, flags=re.I)
        if match:
            return _normalize_lottery_identity_value(match.group(1))
    return ""


def _extract_numbered_lottery_prizes(raw: str) -> list[str]:
    prizes: list[str] = []
    in_prizes = False
    for line in raw.splitlines():
        candidate = line.strip()
        if not in_prizes:
            if re.search(r"奖品(?:内容)?\s*[:：]?\s*$", candidate):
                in_prizes = True
            continue
        if not candidate:
            continue
        match = re.match(r"^\s*\d+\s*[.、)]\s*(.+?)\s*$", candidate)
        if not match:
            if prizes:
                break
            continue
        prize = _normalize_lottery_identity_value(match.group(1))
        if prize:
            prizes.append(prize)
    return sorted(prizes)


LOTTERY_SECTION_LABELS = (
    "截止时间",
    "奖品",
    "奖品内容",
    "发布群组",
    "抽奖条件",
    "参与要求",
    "口令",
    "活动详情",
    "活动说明",
    "开奖条件",
    "创建者",
    "发起人",
    "参与关键词",
    "定时开奖",
    "开奖时间",
)


def _extract_lottery_section_values(raw: str, labels: tuple[str, ...]) -> list[str]:
    label_pattern = "|".join(re.escape(label) for label in labels)
    section_pattern = "|".join(re.escape(label) for label in LOTTERY_SECTION_LABELS)
    lines = (raw or "").splitlines()
    for index, line in enumerate(lines):
        candidate = re.sub(r"^[^\w\u3400-\u9fff]+", "", line.strip())
        match = re.match(rf"(?:{label_pattern})\s*[:：]\s*(.*?)\s*$", candidate, flags=re.I)
        if not match:
            continue
        inline = _normalize_lottery_identity_value(match.group(1))
        if inline:
            return [inline]

        values: list[str] = []
        for following in lines[index + 1:]:
            stripped = following.strip()
            if not stripped:
                continue
            next_candidate = re.sub(r"^[^\w\u3400-\u9fff]+", "", stripped)
            if re.match(rf"(?:{section_pattern})\s*[:：]", next_candidate, flags=re.I):
                break
            if re.fullmatch(r"[━─—=\-]+", stripped) or "祝所有参与者好运" in stripped:
                break
            if re.search(r"(?:现在|当前)[^\n]{0,40}参与|参与[^\n]{0,40}抽奖|祝[^\n]{0,40}好运", next_candidate):
                break
            value = _normalize_lottery_identity_value(next_candidate)
            if value:
                values.append(value)
        return sorted(set(values))
    return []


def _extract_lottery_title(raw: str) -> str:
    for line in (raw or "").splitlines():
        value = _normalize_lottery_identity_value(line)
        if value and "抽奖活动已开始" not in value and not re.fullmatch(r"[━─—=\-]+", value):
            return value
    return ""


def _normalize_scratch_prize_value(value: str) -> str:
    normalized = _normalize_lottery_identity_value(value)
    quantity_match = re.search(r"x(\d+)$", normalized, flags=re.I)
    quantity = int(quantity_match.group(1) or 1) if quantity_match else 1
    prize = normalized[:quantity_match.start()] if quantity_match else normalized
    if not prize:
        return ""

    # Drop a descriptive prefix before a leading numeric prize, while keeping
    # signed coin prizes and code-like hyphenated values intact.
    first_digit = re.search(r"\d", prize)
    suffix_after_digit = prize[first_digit.start():] if first_digit else ""
    if (
        first_digit
        and first_digit.start() > 0
        and not prize.startswith(("+", "-"))
        and not re.search(r"[-_]", prize[:first_digit.start()])
        and re.search(r"币|元代金券|元|积分|点券|钻石|券", suffix_after_digit)
    ):
        prize = prize[first_digit.start():]
    return f"{prize}x{quantity}"


def _extract_scratch_prizes(raw: str) -> list[str]:
    prizes = _extract_lottery_section_values(raw, ("奖品", "奖品内容"))
    normalized = {
        _normalize_scratch_prize_value(prize)
        for prize in prizes
    }
    return sorted(prize for prize in normalized if prize)


def _scratch_prize_base(value: str) -> str:
    prize = re.sub(r"x\d+$", "", value or "")
    prize = re.sub(r"\d+\s*(?:个|份|张|枚|条|次|位|组)?$", "", prize)
    return re.sub(r"\s+", "", prize).strip()


def extract_lottery_template_identity(text: str, message_link: str = "", source: str = "") -> str:
    """Correlate high-confidence lottery template variants for a short window."""
    raw = unicodedata.normalize("NFKC", text or "")
    creator = _extract_lottery_line_value(raw, ("创建者", "发起人"))
    keyword = _extract_lottery_line_value(raw, ("参与关键词",))
    draw_time = _extract_lottery_line_value(raw, ("定时开奖", "开奖时间"))
    numbered_prizes = _extract_numbered_lottery_prizes(raw)
    if creator and keyword and draw_time and len(numbered_prizes) >= 2:
        return (
            f"event:creator:{creator}|keyword:{keyword}|draw:{draw_time}"
            f"|prizes:{'|'.join(numbered_prizes)}"
        )

    title = _extract_lottery_title(raw)
    prizes = _extract_lottery_section_values(raw, ("奖品", "奖品内容"))
    requirement = _extract_lottery_line_value(raw, ("参与要求",))
    details = _extract_lottery_section_values(raw, ("活动说明",))
    if title and draw_time and prizes and requirement and details:
        return (
            f"aq-event:title:{title}|draw:{draw_time}|prizes:{'|'.join(prizes)}"
            f"|requirement:{requirement}|details:{'|'.join(details)}"
        )

    if "抽奖活动已开始" in raw:
        deadline = _extract_lottery_line_value(raw, ("截止时间",))
        prizes = _extract_lottery_section_values(raw, ("奖品", "奖品内容"))
        publish_groups = _extract_lottery_section_values(raw, ("发布群组",))
        passphrase = _extract_lottery_line_value(raw, ("口令",))
        details = _extract_lottery_section_values(raw, ("活动详情",))
        if title and deadline and prizes and publish_groups and passphrase and details:
            return (
                f"deadline:{deadline}|title:{title}|publish:{'|'.join(publish_groups)}"
                f"|prizes:{'|'.join(prizes)}|passphrase:{passphrase}|details:{'|'.join(details)}"
            )
        if title and deadline and prizes:
            return (
                f"started:title:{title}|deadline:{deadline}|prizes:{'|'.join(prizes)}"
            )

    if "刮刮乐" not in raw:
        return ""

    if classify_activity(raw) in {"lottery", "joint_lottery"}:
        prizes = _extract_scratch_prizes(raw)
        if prizes:
            return f"scratch-event:prizes:{'|'.join(prizes)}"

    return ""


def extract_lottery_global_identity(text: str) -> str:
    """Broad global lottery identity based on stable public fields.

    Unlike template identities, this ignores passphrase/details/publish group
    ordering so crosspost variants still correlate within the 10-minute window.
    """
    raw = unicodedata.normalize("NFKC", text or "")
    if "刮刮乐" in raw:
        prizes = _extract_scratch_prizes(raw)
        bases = sorted({
            _scratch_prize_base(prize)
            for prize in prizes
            if _scratch_prize_base(prize)
        })
        if bases:
            return f"global-lottery:mode:刮刮乐|prizes:{'|'.join(bases)}"
    title = _extract_lottery_title(raw)
    if not title:
        return ""
    anchor = (
        _extract_lottery_line_value(raw, ("定时开奖", "开奖时间"))
        or _extract_lottery_line_value(raw, ("截止时间",))
    )
    if not anchor:
        return ""
    prizes = _extract_lottery_section_values(raw, ("奖品", "奖品内容"))
    if not prizes:
        return ""
    return (
        f"global-lottery:title:{title}|anchor:{anchor}|prizes:{'|'.join(prizes)}"
    )


def ttl_minutes_for_activity(activity: str, fallback: int | None = None) -> int:
    defaults = {
        "register": 20,
        "invite": 0,
        "code": 20,
        "lottery": 720,
        "joint_lottery": 4320,
        "long_term": 10080,
        "other": 20,
    }
    keys = {
        "register": "dedup_register_minutes",
        "invite": "dedup_invite_minutes",
        "code": "dedup_code_minutes",
        "lottery": "dedup_lottery_minutes",
        "joint_lottery": "dedup_joint_lottery_minutes",
        "long_term": "dedup_long_term_minutes",
        "other": "dedup_other_minutes",
    }
    if fallback is not None:
        defaults["other"] = int(fallback)
    key = keys.get(activity, "dedup_other_minutes")
    default = defaults.get(activity, defaults["other"])
    # Check module-level cache first (TTL values rarely change)
    now = time.time()
    cached = _TTL_CACHE.get(key)
    if cached and now - cached[0] <= _TTL_CACHE_TTL:
        return cached[1]
    try:
        value = max(0, int(r.get(key) or default))
    except Exception:
        value = default
    _TTL_CACHE[key] = (now, value)
    return value


def ttl_minutes_for_profile(profile: dict[str, Any], fallback: int | None = None) -> int:
    activity = str(profile.get("activity") or "other")
    if profile.get("ttl_policy") == "long_term" and activity in {"lottery", "joint_lottery", "register", "invite", "other"}:
        return ttl_minutes_for_activity("long_term", fallback)
    return ttl_minutes_for_activity(activity, fallback)


# ---- Text normalization ----

REGISTER_RENEW_SUFFIX_TOKEN_PATTERN = r"[^\s*`]"
REGISTER_RENEW_SUFFIX_BOUNDARY = (
    r"(?=$|\s|[，。！？？；：、）】]"
    r"|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
REGISTER_RENEW_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])([^\s/?&=#]+(?:-[^\s/?&=#]+)*-\d+-(?:Register|Renew)_"
    + REGISTER_RENEW_SUFFIX_TOKEN_PATTERN
    + r"+?)"
    + REGISTER_RENEW_SUFFIX_BOUNDARY,
    re.I,
)

SOURCE_LINE_HINTS = [
    "官方频道", "频道", "交流群", "官方群", "订阅", "群组", "发布", "通知频道"
]


def _register_renew_code_fingerprints(text: str) -> list[str]:
    fingerprints: list[str] = []
    seen: set[str] = set()
    codes = [
        match.group(1).strip()
        for match in REGISTER_RENEW_CODE_RE.finditer(text or "")
    ]
    codes.extend(extract_telegram_start_register_renew_codes(text))
    for code in codes:
        if not code or code in seen:
            continue
        seen.add(code)
        fingerprints.append("rrc" + hashlib.sha256(code.encode("utf-8")).hexdigest()[:16])
    return sorted(fingerprints)


def _invite_path_code_fingerprints(text: str) -> list[str]:
    return sorted(
        "ipc" + hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
        for code in extract_invite_path_codes(text)
    )


def _is_probable_source_line(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return True
    compact = re.sub(r"\s+", "", raw)
    if len(compact) > 36:
        return False
    low = raw.lower()
    has_hint = any(h.lower() in low for h in SOURCE_LINE_HINTS)
    return bool(has_hint)


def normalize_for_text_dedup(text: str) -> str:
    """Standardize text for dedup: NFKC -> strip invisible chars -> drop source lines
    -> drop t.me links -> drop emoji -> drop punctuation -> lowercase -> compress whitespace.

    Also normalizes dynamic content so the same announcement posted multiple times
    (with different timestamps, participant counts, seed hashes, etc.) gets the
    same hash and is deduplicated.
    """
    raw = unicodedata.normalize("NFKC", text or "")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060]", "", raw)
    register_code_fingerprints = _register_renew_code_fingerprints(raw)
    invite_path_code_fingerprints = _invite_path_code_fingerprints(raw)

    # ---- Dynamic-content normalization (order matters) ----

    # Parse lines; drop pure meta lines (timestamps, seed hashes, stats)
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    kept: list[str] = []
    for idx, line in enumerate(lines):
        if idx <= 5 and _is_probable_source_line(line):
            continue
        if re.fullmatch(r"(?:https?://)?t\.me/\S+", line, flags=re.I):
            continue
        low = line.lower()
        # Drop pure-meta lines: timestamps, hashes, stats
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", line):
            continue
        if low.startswith("随机种子哈希:") or low.startswith("random seed:"):
            continue
        if re.search(r"已参与[：:\s]*\d+人", line):
            continue
        if re.search(r"中奖概率[：:\s]*[\d.]+%?", line):
            continue
        if re.search(r"(?:当前)?参与人数[：:\s]*\d+", line):
            continue
        if re.search(r"消耗\s+[\d.]+\s+碎片", line):
            continue
        if re.search(r"(?:满|满员|已满|满额)\s*\d*人?", line):
            continue
        # Drop "Forwarded from" / "转发自" header lines (any position)
        if re.search(r'(?i)^(forwarded from|转发自)\b', line):
            continue
        kept.append(line)
    base = "\n".join(kept) if kept else raw

    # Strip HTML entities
    base = base.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")
    # Strip Telegram Markdown markup
    base = re.sub(r'\*\*|__|~~|`', ' ', base)

    # Normalize URLs -> [URL]
    base = re.sub(r"https?://\S+", " [URL] ", base)
    base = re.sub(r"t\.me/\S+", " [URL] ", base, flags=re.I)
    if register_code_fingerprints:
        base += "\nregister renew code fingerprints " + " ".join(register_code_fingerprints)
    if invite_path_code_fingerprints:
        base += "\ninvite path code fingerprints " + " ".join(invite_path_code_fingerprints)

    # Normalize UUIDs -> [UUID]
    base = re.sub(r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b", " [UUID] ", base, flags=re.I)

    # Normalize hex hashes (40+ chars) -> [HASH]
    base = re.sub(r"\b[a-f0-9]{40,}\b", " [HASH] ", base, flags=re.I)

    # Normalize timestamps -> [DATE] / [TIME]
    base = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " [DATE] ", base)
    base = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " [TIME] ", base)

    # Normalize fractions -> [FRAC] (before number normalization)
    base = re.sub(r"\b\d+/\d+\b", " [FRAC] ", base)
    # Normalize percentages / decimals -> [PCT]
    base = re.sub(r"\b\d+\.\d+%?\b", " [PCT] ", base)
    base = re.sub(r"\b\d+%\b", " [PCT] ", base)
    # Normalize numbers (4+ digits) -> [NUM]
    base = re.sub(r"\b\d{4,}\b", " [NUM] ", base)

    base = EMOJI_RE.sub(" ", base)
    base = re.sub(r"[\[\]【】（）(){}<>《》|:：;；,，。.!！?？#*_`~+=/\\\-@&%$]", " ", base)
    base = re.sub(r"\s+", " ", base).strip().lower()
    return base


# ---- Profile building ----

def build_profile(text: str, message_link: str = "", source: str = "") -> dict[str, Any]:
    """Build a lightweight activity profile for TTL calculation and UI display."""
    activity = classify_activity(text)
    ttl_policy = ttl_policy_for_text(text)
    normalized = normalize_for_text_dedup(text)
    text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    lottery_identity = extract_lottery_identity(text) if activity in {"lottery", "joint_lottery"} else ""
    lottery_global_identity = (
        extract_lottery_global_identity(text)
        if activity in {"lottery", "joint_lottery"} and not lottery_identity
        else ""
    )
    lottery_template_identity = (
        extract_lottery_template_identity(text, message_link, source)
        if activity in {"lottery", "joint_lottery"}
        else ""
    )
    if lottery_identity:
        identity_hash = hashlib.sha256(lottery_identity.encode("utf-8")).hexdigest()
        dedup_id = "lottery:" + identity_hash
        dedup_strategy = "lottery_identity"
    else:
        dedup_id = "text:" + text_hash
        dedup_strategy = "normalized_text"

    return {
        "activity": activity,
        "ttl_policy": ttl_policy,
        "core": normalized[:300],
        "dedup_id": dedup_id,
        "message_link": message_link,
        "text_hash": text_hash,
        "lottery_identity": lottery_identity,
        "lottery_global_identity": lottery_global_identity,
        "lottery_template_identity": lottery_template_identity,
        "lottery_mode": "id" if lottery_identity else "",
        "dedup_strategy": dedup_strategy,
    }


# ---- Unified two-layer dedup ----

def check_and_mark(
    text: str,
    message_link: str,
    ttl_minutes: int | None = 20,
    mode: str = "strict",
    source: str = "",
) -> tuple[bool, str, dict]:
    """Unified dedup with two layers, both checked in a single Redis pipeline.

    Layer 1: Same original message link -> block.
    Layer 2: Same stable lottery identity or normalized text hash -> block.
    """
    profile = build_profile(text, message_link, source)
    content_key = "dedup:" + profile["dedup_id"]
    link_key = "dedup:link:" + sha(message_link) if message_link else ""
    template_identities: list[str] = []
    if lottery_template_dedup_mode() == "global":
        seen_identities: set[str] = set()
        for identity in (
            profile.get("lottery_template_identity") or "",
            profile.get("lottery_global_identity") or "",
        ):
            if identity and identity not in seen_identities:
                seen_identities.add(identity)
                template_identities.append(identity)
    effective_template_identities = [
        identity
        for identity in template_identities
        if not _is_collision_exempt(identity, profile["dedup_id"])
    ]
    template_keys = [
        "dedup:lottery-template:" + sha(identity)
        for identity in effective_template_identities
    ]

    real_ttl_minutes = ttl_minutes_for_profile(profile, ttl_minutes) if ttl_minutes is None else int(ttl_minutes)
    if int(real_ttl_minutes) <= 0:
        return False, "该类型已设置为不去重", profile
    ttl_seconds = max(60, int(real_ttl_minutes) * 60)
    dedup_id = profile["dedup_id"]

    # Pipeline text+link SET NX in one roundtrip.
    # SET NX stores dedup_id as value, so the stored value is always dedup_id
    # — we never need a follow-up GET for existing_id.
    pipe = r.pipeline()
    pipe.set(content_key, dedup_id, ex=ttl_seconds, nx=True)
    if message_link:
        pipe.set(link_key, dedup_id, ex=ttl_seconds, nx=True)
    for template_key in template_keys:
        pipe.set(template_key, dedup_id, ex=LOTTERY_TEMPLATE_WINDOW_SECONDS, nx=True)
    results = pipe.execute()

    content_is_new = results[0]
    if not content_is_new:
        if profile.get("lottery_identity"):
            reason = f"相同抽奖 ID 重复（{real_ttl_minutes}分钟内）"
        else:
            reason = f"相同文本内容重复（{real_ttl_minutes}分钟内）"
        _record_duplicate(dedup_id, profile, reason, source, message_link, ttl_seconds)
        return True, reason, profile

    result_index = 1
    link_is_new = results[result_index] if message_link else True
    result_index += 1 if message_link else 0

    if not link_is_new:
        reason = f"同一条原消息链接重复（{real_ttl_minutes}分钟内）"
        _record_duplicate(dedup_id, profile, reason, source, message_link, ttl_seconds)
        return True, reason, profile

    template_new: list[bool] = []
    for template_index, template_key in enumerate(template_keys):
        template_is_new = results[result_index]
        result_index += 1
        template_new.append(template_is_new)
        if not template_is_new:
            existing_id = r.get(template_key) or dedup_id
            explicit_id_conflict = (
                bool(profile.get("lottery_identity"))
                and str(existing_id).startswith("lottery:")
                and existing_id != dedup_id
            )
            if not explicit_id_conflict:
                reason = "同一抽奖的不同模板重复（10分钟内）"
                _add_lottery_collision({
                    "identity": effective_template_identities[template_index],
                    "dedup_id": profile["dedup_id"],
                    "first_dedup_id": existing_id,
                    "source": source or "",
                    "link": message_link or profile.get("message_link", ""),
                })
                _record_duplicate(existing_id, profile, reason, source, message_link, ttl_seconds)
                return True, reason, profile

    redis_keys = [content_key]
    if link_key:
        redis_keys.append(link_key)
    for template_key, template_is_new in zip(template_keys, template_new):
        if template_is_new:
            redis_keys.append(template_key)
    _register_new(profile, redis_keys, ttl_seconds, source, "首次命中")
    return False, "未重复", profile


def _register_new(profile: dict[str, Any], redis_keys: list[str], ttl_seconds: int, source: str, reason: str) -> None:
    expire_at = _now() + ttl_seconds
    meta = {
        "dedup_id": profile["dedup_id"],
        "activity": profile.get("activity", ""),
        "core": profile.get("core", "")[:300],
        "message_link": profile.get("message_link", ""),
        "first_source": source or "",
        "duplicate_count": 0,
        "duplicate_sources": [],
        "first_seen": _ts(),
        "last_seen": _ts(),
        "expire_at": expire_at,
        "redis_keys": redis_keys,
    }
    _save_meta(meta, ttl_seconds)
    _push_recent({
        "action": "first",
        "dedup_id": profile["dedup_id"],
        "activity": profile.get("activity", ""),
        "source": source or "",
        "reason": reason,
        "core": profile.get("core", "")[:180],
        "link": profile.get("message_link", ""),
        "expire_at": expire_at,
    })


def _record_duplicate(dedup_id: str, profile: dict[str, Any], reason: str, source: str, link: str, ttl_seconds: int) -> None:
    meta = _load_meta(dedup_id)
    if meta:
        sources = list(meta.get("duplicate_sources") or [])
        if source and source not in sources and source != meta.get("first_source"):
            sources.append(source)
        meta["duplicate_sources"] = sources[-30:]
        meta["duplicate_count"] = int(meta.get("duplicate_count") or 0) + 1
        meta["last_seen"] = _ts()
        remain = max(60, int(meta.get("expire_at", _now() + ttl_seconds)) - _now())
        _save_meta(meta, remain)
    _push_recent({
        "action": "duplicate",
        "dedup_id": dedup_id,
        "activity": profile.get("activity", ""),
        "source": source or "",
        "reason": reason,
        "core": profile.get("core", "")[:180],
        "link": link or profile.get("message_link", ""),
    })


# ---- WebUI helpers ----

def list_dedup_recent(limit: int = 30) -> list[dict[str, Any]]:
    out = []
    seen = set()
    now = _now()
    for raw in r.lrange(RECENT_LIST, 0, max(limit * 4, limit) - 1):
        try:
            item = json.loads(raw)
        except Exception:
            continue
        did = item.get("dedup_id") or ""
        if not did or did in seen:
            continue
        seen.add(did)
        meta = _load_meta(did)
        item["duplicate_count"] = int(meta.get("duplicate_count") or 0) if meta else int(item.get("duplicate_count") or 0)
        item["first_source"] = meta.get("first_source", "") if meta else ""
        item["duplicate_sources"] = meta.get("duplicate_sources", []) if meta else []
        expire_at = int(meta.get("expire_at") or item.get("expire_at") or 0) if (meta or item) else 0
        item["ttl_left"] = max(0, expire_at - now) if expire_at else 0
        item["active"] = bool(meta)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def release_dedup(dedup_id: str) -> bool:
    dedup_id = (dedup_id or "").strip()
    if not dedup_id:
        return False
    meta = _load_meta(dedup_id)
    keys = list(meta.get("redis_keys") or []) if meta else []
    if not meta:
        # Meta expired or was lost; remove any dedup key still pointing at this id.
        try:
            for k in r.scan_iter(match="dedup:*", count=200):
                try:
                    if str(r.type(k) or "") == "string" and r.get(k) == dedup_id:
                        keys.append(k)
                except Exception:
                    continue
        except Exception:
            pass
    keys.append(_meta_key(dedup_id))
    # dedup_id is "text:<text_hash>"; the actual Redis key is "dedup:" + dedup_id.
    # Link keys are stored in meta.redis_keys when available; we cannot
    # reconstruct them without the original message_link, so rely on meta.
    keys.append("dedup:" + dedup_id)
    r.delete(*[k for k in set(keys) if k])
    _push_recent({"action": "release", "dedup_id": dedup_id, "reason": "手动解除去重"})
    return True
