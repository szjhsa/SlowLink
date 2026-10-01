import re
from urllib.parse import parse_qs, urlsplit


TELEGRAM_HOSTS = {"t.me", "telegram.me"}
TELEGRAM_BOT_LINK_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<url>(?:(?:https?://)?(?:t\.me|telegram\.me)/"
    r"[A-Za-z0-9_]{5,64}(?:\?[^\s<>'\"]*)?))",
    re.I,
)
START_PAYLOAD_TOKEN_PATTERN = r"[^\s*`\u3400-\u9fff]"
DAYS_FIRST_REGISTER_RENEW_RE = re.compile(
    r"^\d{1,5}(?:-" + START_PAYLOAD_TOKEN_PATTERN + r"+)*"
    r"-(?:Register|Renew)_" + START_PAYLOAD_TOKEN_PATTERN + r"+$",
    re.I,
)
TRAILING_LINK_PUNCTUATION = ".,;:!?)]}>`~，。！？？；：、）】"


def extract_telegram_start_register_renew_codes(text: str) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for match in TELEGRAM_BOT_LINK_RE.finditer(text or ""):
        candidate = (match.group("url") or "").rstrip(TRAILING_LINK_PUNCTUATION)
        if not re.match(r"(?i)^https?://", candidate):
            candidate = "https://" + candidate
        try:
            parsed = urlsplit(candidate)
            host = (parsed.hostname or "").lower()
            bot_username = parsed.path.strip("/")
            if host not in TELEGRAM_HOSTS:
                continue
            if not bot_username or "/" in bot_username or not bot_username.lower().endswith("bot"):
                continue
            start_values = parse_qs(parsed.query, keep_blank_values=True).get("start", [])
        except (TypeError, ValueError):
            continue
        for value in start_values:
            code = (value or "").strip()
            if len(code) > 64 or not DAYS_FIRST_REGISTER_RENEW_RE.fullmatch(code):
                continue
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


def extract_first_telegram_start_register_renew_code(text: str) -> str:
    codes = extract_telegram_start_register_renew_codes(text)
    return codes[0] if codes else ""
