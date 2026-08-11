"""Shared strong-code patterns used by matcher, code_rules and dedup."""

HYPHEN_REGISTER_RENEW_PATTERN = (
    r"(?<![A-Za-z0-9-])[^\s*`-]+-(?:Register|Renew)-"
    r"[^\s*`-]+(?:-[^\s*`-]+)+"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
