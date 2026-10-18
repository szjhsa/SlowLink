"""Bounded wildcard-aware identity indexes for masked code dedup."""

import re
from functools import lru_cache


WILDCARD_IDENTITY_PREFIX = "wildcard:"
IDENTITY_RECENT_KEY = "dedup:identity_recent"
WILDCARD_PATTERNS_KEY = "dedup:wildcard_identity_patterns"
MAX_RECENT_IDENTITIES = 500
MAX_WILDCARD_PATTERNS = 200
INDEX_TTL_SECONDS = 24 * 60 * 60


def is_wildcard_identity(identity: str) -> bool:
    return str(identity or "").startswith(WILDCARD_IDENTITY_PREFIX)


@lru_cache(maxsize=512)
def _compiled_pattern(pattern: str):
    expression = "".join(
        "[^\\s*]" if char == "*" else re.escape(char)
        for char in str(pattern or "")
    )
    return re.compile(rf"^{expression}$", re.IGNORECASE)


def wildcard_matches(pattern: str, candidate: str) -> bool:
    if not is_wildcard_identity(pattern) or not candidate:
        return False
    if "*" not in pattern:
        return False
    _pattern_prefix, pattern_sep, pattern_value = str(pattern).partition(":")
    _candidate_prefix, candidate_sep, candidate_value = str(candidate).partition(":")
    pattern_value = pattern_value if pattern_sep else str(pattern)
    candidate_value = candidate_value if candidate_sep else str(candidate)
    try:
        return bool(_compiled_pattern(pattern_value).fullmatch(candidate_value))
    except Exception:
        return False


def _active_members(redis_client, key: str, now: float, limit: int) -> list[str]:
    try:
        redis_client.zremrangebyscore(key, "-inf", now)
        redis_client.zremrangebyrank(key, 0, -(limit + 1))
        return [
            str(value)
            for value in redis_client.zrangebyscore(
                key,
                now,
                "+inf",
                start=0,
                num=limit,
            )
        ]
    except Exception:
        return []


def find_identity_match(
    redis_client,
    identity: str,
    now: float,
) -> str:
    identity = str(identity or "")
    if not identity:
        return ""
    if is_wildcard_identity(identity):
        for recent in _active_members(
            redis_client,
            IDENTITY_RECENT_KEY,
            now,
            MAX_RECENT_IDENTITIES,
        ):
            if wildcard_matches(identity, recent):
                return recent
        return ""
    for pattern in _active_members(
        redis_client,
        WILDCARD_PATTERNS_KEY,
        now,
        MAX_WILDCARD_PATTERNS,
    ):
        if wildcard_matches(pattern, identity):
            return pattern
    return ""


def remember_identity(
    redis_client,
    identity: str,
    expires_at: float,
) -> None:
    identity = str(identity or "")
    if not identity:
        return
    key = WILDCARD_PATTERNS_KEY if is_wildcard_identity(identity) else IDENTITY_RECENT_KEY
    limit = MAX_WILDCARD_PATTERNS if is_wildcard_identity(identity) else MAX_RECENT_IDENTITIES
    try:
        redis_client.zadd(key, {identity: float(expires_at)})
        redis_client.zremrangebyscore(key, "-inf", 0)
        redis_client.zremrangebyrank(key, 0, -(limit + 1))
        redis_client.expire(key, INDEX_TTL_SECONDS)
    except Exception:
        pass


def release_identity(redis_client, identity: str) -> None:
    identity = str(identity or "")
    if not identity:
        return
    key = WILDCARD_PATTERNS_KEY if is_wildcard_identity(identity) else IDENTITY_RECENT_KEY
    try:
        redis_client.zrem(key, identity)
    except Exception:
        pass
