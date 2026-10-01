"""Plugin-owned Redis defaults, migrations, and storage keys."""

DEFAULTS = {
    "dedup_invite_minutes": "0",
    "dedup_register_minutes": "20",
    "dedup_code_minutes": "20",
    "dedup_lottery_minutes": "720",
    "dedup_joint_lottery_minutes": "4320",
    "dedup_long_term_minutes": "10080",
    "dedup_other_minutes": "20",
    "dedup_lottery_template_mode": "global",
    "dedup_lottery_key_mode": "id",
}

STATS_HASH_KEYS = (
    "stats:rule_hits",
    "stats:rule_duplicates",
    "stats:source_hits",
    "stats:source_duplicates",
    "stats:lottery_collisions",
)
COLLISION_STATS_KEY = "stats:lottery_collisions"
COLLISION_LIST = "dedup:collisions"
COLLISION_EXEMPT_PREFIX = "dedup:collision_exempt:"
ACTIVE_DEDUP_PATTERNS = [
    "dedup:main:*",
    "dedup:core:*",
    "dedup:link:*",
    "dedup:code:*",
    "dedup:identity:*",
    "dedup:lottery:*",
    "dedup:lottery-template:*",
    "dedup:register_snapshot:*",
    "dedup:content_url:*",
    "dedup:text:*",
    "dedup:collision_exempt:*",
]
DEDUP_META_PATTERNS = ["dedup:meta:*"]

LEGACY_PURE_CODE_TRIGGER_RULE = r"^(?!.*码使用)[^-]+-\d+-(?:Register|Renew)_.+$"
LEGACY_SAFE_PURE_CODE_TRIGGER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_[A-Za-z0-9_-]+$"
)
LEGACY_MASKED_PURE_CODE_TRIGGER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_(?:[A-Za-z0-9_-]|数字|字母)+$"
)
LEGACY_SYMBOL_PURE_CODE_TRIGGER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_(?:[^\s*`\u3400-\u9fff]|数字|字母)+$"
)
SAFE_PURE_CODE_TRIGGER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_[^\s*`]+$"
)
LEGACY_SAFE_WHITELIST_TRIGGER_RULE = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-Whitelist_"
    r"(?a:[A-Za-z0-9]{10})"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
LEGACY_GUESS_WHITELIST_TRIGGER_RULE = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-Whitelist_"
    r"(?=[^\s*`]*[\u3400-\u9fff])"
    r"(?=(?:[^A-Za-z0-9\s*`]*[A-Za-z0-9]){10}[^A-Za-z0-9\s*`]*(?=$|\s))"
    r"[^\s*`]+?"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
SAFE_WHITELIST_TRIGGER_RULE = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-Whitelist_"
    r"(?:"
    r"(?a:[A-Za-z0-9]{10})"
    r"|"
    r"(?=[^\s*`]*[\u3400-\u9fff])"
    r"(?=(?:[^A-Za-z0-9\s*`]*[A-Za-z0-9]){10}[^A-Za-z0-9\s*`]*(?=$|\s))"
    r"[^\s*`]+?"
    r")"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
LEGACY_REGISTRATION_ANNOUNCEMENT_RULE = (
    r"((?:[🫧🎫🎟️🎭🤖⏳].*?(?:自由|定时)注册.*(?:\n[🫧🎫🎟️🎭🤖⏳].*\|\s*\d+.*)*\n?)+)"
    r"|((?:[🎉✨📱⏰].*?开放注册.*(?:\n[🎉✨📱⏰].*)*\n?)+)"
)
SAFE_REGISTRATION_ANNOUNCEMENT_RULE = (
    r"(?m)^(?:[🫧🎫🎟️🎭🤖⏳][^\n]*(?:自由|定时)注册"
    r"|[🎉✨📱⏰][^\n]*开放注册)[^\n]*$"
)
LEGACY_OPEN_REGISTRATION_STATE_RULE = r"(?m)^[^\n]*(?:当前)?开注状态[：:]\s*True"
SAFE_OPEN_REGISTRATION_STATE_RULE = (
    r"(?m)^[^\n]*(?:当前)?开注状态\s*(?:[|｜:：]\s*)"
    r"(?:True|ON|开启|开放|1|已开启)"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*])"
)
LEGACY_LOTTERY_ACTIVITY_RULE = r"\n\n🎁 抽奖活动已开始"
SAFE_LOTTERY_ACTIVITY_RULE = r"(?m)^抽奖活动已开始！?$"
LEGACY_PRIZE_CONTENT_TRIGGER_RULE = r"\n\n🎁 奖品内容"
SAFE_PRIZE_CONTENT_TRIGGER_RULE = (
    r"(?m)^\n?🎁\s*\**\s*奖品内容\s*(?:[:：]\s*)?"
)
LEGACY_COMBINED_PRIZE_LOTTERY_RULE = r"🎁 抽奖开始啦;;\n\n🎁 奖品内容"
SAFE_COMBINED_PRIZE_LOTTERY_RULE = (
    r"🎁 抽奖开始啦;;" + SAFE_PRIZE_CONTENT_TRIGGER_RULE
)

KNOWN_REGEX_RULE_MIGRATIONS = {
    LEGACY_PURE_CODE_TRIGGER_RULE: SAFE_PURE_CODE_TRIGGER_RULE,
    LEGACY_SAFE_PURE_CODE_TRIGGER_RULE: SAFE_PURE_CODE_TRIGGER_RULE,
    LEGACY_MASKED_PURE_CODE_TRIGGER_RULE: SAFE_PURE_CODE_TRIGGER_RULE,
    LEGACY_SYMBOL_PURE_CODE_TRIGGER_RULE: SAFE_PURE_CODE_TRIGGER_RULE,
    LEGACY_SAFE_WHITELIST_TRIGGER_RULE: SAFE_WHITELIST_TRIGGER_RULE,
    LEGACY_GUESS_WHITELIST_TRIGGER_RULE: SAFE_WHITELIST_TRIGGER_RULE,
    LEGACY_REGISTRATION_ANNOUNCEMENT_RULE: SAFE_REGISTRATION_ANNOUNCEMENT_RULE,
    LEGACY_OPEN_REGISTRATION_STATE_RULE: SAFE_OPEN_REGISTRATION_STATE_RULE,
    LEGACY_LOTTERY_ACTIVITY_RULE: SAFE_LOTTERY_ACTIVITY_RULE,
    LEGACY_PRIZE_CONTENT_TRIGGER_RULE: SAFE_PRIZE_CONTENT_TRIGGER_RULE,
    LEGACY_COMBINED_PRIZE_LOTTERY_RULE: SAFE_COMBINED_PRIZE_LOTTERY_RULE,
}


def storage_config() -> dict:
    return {
        "defaults": dict(DEFAULTS),
        # Core reads these opaque names; all business naming stays in the plugin.
        "correlation_mode_key": "dedup_lottery_template_mode",
        "correlation_mode_default": "global",
        "correlation_mode_policy_field": "lottery_template_mode",
        "identity_ttl_key": "dedup_code_minutes",
        "identity_ttl_default": 20,
        "identity_key_prefix": "dedup:identity:",
        "identity_dedup_policy_field": "code_dedup",
        "identity_dedup_default": False,
        "stats_hash_keys": list(STATS_HASH_KEYS),
        "collision_stats_key": COLLISION_STATS_KEY,
        "collision_list": COLLISION_LIST,
        "collision_exempt_prefix": COLLISION_EXEMPT_PREFIX,
        "active_dedup_patterns": list(ACTIVE_DEDUP_PATTERNS),
        "dedup_meta_patterns": list(DEDUP_META_PATTERNS),
        "known_regex_rule_migrations": dict(KNOWN_REGEX_RULE_MIGRATIONS),
        "aliases": {
            "LEGACY_PURE_CODE_TRIGGER_RULE": LEGACY_PURE_CODE_TRIGGER_RULE,
            "LEGACY_SAFE_PURE_CODE_TRIGGER_RULE": LEGACY_SAFE_PURE_CODE_TRIGGER_RULE,
            "LEGACY_MASKED_PURE_CODE_TRIGGER_RULE": LEGACY_MASKED_PURE_CODE_TRIGGER_RULE,
            "LEGACY_SYMBOL_PURE_CODE_TRIGGER_RULE": LEGACY_SYMBOL_PURE_CODE_TRIGGER_RULE,
            "SAFE_PURE_CODE_TRIGGER_RULE": SAFE_PURE_CODE_TRIGGER_RULE,
            "LEGACY_SAFE_WHITELIST_TRIGGER_RULE": LEGACY_SAFE_WHITELIST_TRIGGER_RULE,
            "LEGACY_GUESS_WHITELIST_TRIGGER_RULE": LEGACY_GUESS_WHITELIST_TRIGGER_RULE,
            "SAFE_WHITELIST_TRIGGER_RULE": SAFE_WHITELIST_TRIGGER_RULE,
            "LEGACY_REGISTRATION_ANNOUNCEMENT_RULE": LEGACY_REGISTRATION_ANNOUNCEMENT_RULE,
            "SAFE_REGISTRATION_ANNOUNCEMENT_RULE": SAFE_REGISTRATION_ANNOUNCEMENT_RULE,
            "LEGACY_OPEN_REGISTRATION_STATE_RULE": LEGACY_OPEN_REGISTRATION_STATE_RULE,
            "SAFE_OPEN_REGISTRATION_STATE_RULE": SAFE_OPEN_REGISTRATION_STATE_RULE,
            "LEGACY_LOTTERY_ACTIVITY_RULE": LEGACY_LOTTERY_ACTIVITY_RULE,
            "SAFE_LOTTERY_ACTIVITY_RULE": SAFE_LOTTERY_ACTIVITY_RULE,
            "LEGACY_PRIZE_CONTENT_TRIGGER_RULE": LEGACY_PRIZE_CONTENT_TRIGGER_RULE,
            "SAFE_PRIZE_CONTENT_TRIGGER_RULE": SAFE_PRIZE_CONTENT_TRIGGER_RULE,
            "LEGACY_COMBINED_PRIZE_LOTTERY_RULE": LEGACY_COMBINED_PRIZE_LOTTERY_RULE,
            "SAFE_COMBINED_PRIZE_LOTTERY_RULE": SAFE_COMBINED_PRIZE_LOTTERY_RULE,
        },
        "function_aliases": {
            "add_lottery_collision": "add_correlation_collision",
        },
    }
