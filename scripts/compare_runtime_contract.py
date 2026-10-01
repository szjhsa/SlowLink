#!/usr/bin/env python3
"""Print deterministic runtime-contract results for production/test comparison."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback


class FakePipeline:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: self

    def execute(self):
        return []


class FakeRedis:
    def get(self, *_args, **_kwargs):
        return None

    def set(self, *_args, **_kwargs):
        return True

    def setex(self, *_args, **_kwargs):
        return True

    def hgetall(self, *_args, **_kwargs):
        return {}

    def hset(self, *_args, **_kwargs):
        return 1

    def hdel(self, *_args, **_kwargs):
        return 1

    def pipeline(self):
        return FakePipeline()

    def scan_iter(self, *_args, **_kwargs):
        return []

    def lrange(self, *_args, **_kwargs):
        return []

    def delete(self, *_args, **_kwargs):
        return 0

    def smembers(self, *_args, **_kwargs):
        return set()

    def sismember(self, *_args, **_kwargs):
        return False

    def zrangebyscore(self, *_args, **_kwargs):
        return []

    def zcard(self, *_args, **_kwargs):
        return 0


def safe_call(fn, *args, **kwargs):
    try:
        value = fn(*args, **kwargs)
        return value
    except Exception as exc:
        return {
            "error": type(exc).__name__,
            "message": str(exc),
            "trace": traceback.format_exc(limit=1),
        }


def main() -> int:
    sys.path.insert(0, "/app")
    os.environ.setdefault("SLOWLINK_ACTIVE_PLUGIN", "builtin")

    import redis_store

    redis_store.r = FakeRedis()
    redis_store.get = lambda _key, default=None: default
    redis_store.get_json = lambda _key, default=None: default
    redis_store.set_json = lambda *_args, **_kwargs: None
    redis_store.smembers = lambda _key: set()
    redis_store.set_value = lambda *_args, **_kwargs: None
    redis_store.log_line = lambda *_args, **_kwargs: None
    redis_store.sha = lambda value: hashlib.sha256(
        str(value or "").encode("utf-8")
    ).hexdigest()
    redis_store.format_time = lambda *_args, **_kwargs: "2026-01-01 00:00:00"

    import code_rules
    import dedup
    import matcher
    import rule_generator
    import rule_policy

    code_samples = {
        "register_basic": "Wlao-30-Register_jDNZjMTrA",
        "register_chinese": "帝服-30-Register_mK@nxdnuwU",
        "register_masked": "XYING-30-Register_sTUL**Vuvb",
        "register_multi": (
            "NONAY-30-Register_isMSRojTaz\n"
            "NONAY-30-Register_QLmne0nC5W\n"
            "NONAY-30-Register_AiiLXFiEZ1"
        ),
        "whitelist": "WindMoon-Whitelist_1OTb0O0FMO",
        "telegram_start": (
            "https://t.me/Moonkkbot?start=30-Register_seeuhmOtV8"
        ),
        "invite_path": "https://ep.whooping.top/invite/5fbbddb9",
    }
    code_results = {}
    for name, text in code_samples.items():
        code_results[name] = {
            "detail": safe_call(code_rules.extract_code_detail, text),
            "identities": safe_call(code_rules.extract_code_identities, text),
            "trigger": safe_call(code_rules.extract_trigger_code_detail, text),
        }

    matcher_samples = {
        "closed_registration": "注册状态：false",
        "open_registration": "开注状态 | ON",
        "used_code": "注册码使用：ABC-30-Register_123456",
        "registration_success": "注册成功\n创建了账号\n到期时间 2026-01-01",
        "ordinary": "今天天气不错",
    }
    matcher_results = {
        name: safe_call(matcher.analyze_message, text)
        for name, text in matcher_samples.items()
    }

    lottery_a = (
        "新的抽奖已经创建\n"
        "抽奖 ID：1ea59728-11f6-4748-9a32-b341129f2f3d\n"
        "奖品内容：公益服注册码 x1"
    )
    lottery_b = (
        "抽奖信息\n"
        "抽奖 ID: 1ea59728-11f6-4748-9a32-b341129f2f3d\n"
        "当前参与人数：99\n"
        "奖品内容：公益服注册码 x1"
    )
    code_policy = safe_call(rule_policy.default_policy, "code")
    lottery_policy = safe_call(rule_policy.default_policy, "lottery")
    keyword_policy = safe_call(rule_policy.default_policy, "keyword")
    exclude_policy = safe_call(rule_policy.default_policy, "exclude")
    profile_results = {
        "lottery_a": safe_call(dedup.build_profile, lottery_a, policy=lottery_policy),
        "lottery_b": safe_call(dedup.build_profile, lottery_b, policy=lottery_policy),
        "code_basic": safe_call(
            dedup.build_profile,
            code_samples["register_basic"],
            policy=code_policy,
            code_identities=["strong_register_renew:Wlao-30-Register_jDNZjMTrA"],
        ),
        "ordinary": safe_call(dedup.build_profile, matcher_samples["ordinary"]),
    }

    generated_results = {
        "keyword": safe_call(
            rule_generator.generate_rule,
            "keyword",
            "普通关键词公告",
        ),
        "lottery": safe_call(rule_generator.generate_rule, "lottery", lottery_a),
        "code": safe_call(
            rule_generator.generate_rule,
            "code",
            code_samples["register_basic"],
        ),
        "exclude": safe_call(
            rule_generator.generate_rule,
            "exclude",
            "停止注册",
        ),
    }

    result = {
        "version": "unknown",
        "code": code_results,
        "matcher": matcher_results,
        "policies": {
            "code": code_policy,
            "lottery": lottery_policy,
            "keyword": keyword_policy,
            "exclude": exclude_policy,
        },
        "profiles": profile_results,
        "generated_rules": generated_results,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
