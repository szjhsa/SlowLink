"""Shared strong-code patterns used by matcher, code_rules and dedup."""

HYPHEN_REGISTER_RENEW_PATTERN = (
    r"(?<![A-Za-z0-9-])[A-Za-z0-9\u3400-\u9fff]{1,24}-"
    r"(?:Register|Renew)-"
    r"[A-Za-z0-9\u3400-\u9fff]{1,24}"
    r"(?:-[A-Za-z0-9\u3400-\u9fff]{1,24}){1,4}"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
