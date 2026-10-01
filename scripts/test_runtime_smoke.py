#!/usr/bin/env python3
"""Run the main SlowLink runtime smoke checks inside the app container."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime


def check(name: str, condition: bool, detail="") -> dict:
    return {
        "name": name,
        "ok": bool(condition),
        "detail": str(detail),
    }


def main() -> int:
    sys.path.insert(0, "/app")
    os.environ.setdefault("SLOWLINK_ACTIVE_PLUGIN", "builtin")

    import code_rules
    import dedup
    import matcher
    import plugin_registry
    import plugin_runtime
    import rule_generator
    import rule_policy

    checks = [
        check(
            "plugin hooks loaded",
            all(
                plugin_runtime.has_hook(name)
                for name in (
                    "generate_rule",
                    "analyze_match_guards",
                    "extract_code_detail",
                    "extract_code_identities",
                    "build_dedup_profile",
                    "normalize_dedup_text",
                )
            ),
        )
    ]

    code_text = "已为您生成 Wlao-30-Register_jDNZjMTrA"
    code_detail = code_rules.extract_code_detail(code_text)
    checks.append(check("register code extracted", bool(code_detail.get("code")), code_detail))

    multi_text = (
        "已为您生成 Wlao-30-Register_jDNZjMTrA\n"
        "WENJIAN-30-MUSIC-Register_FRK555ddd"
    )
    identities = code_rules.extract_code_identities(multi_text)
    checks.append(check("multiple code identities", len(identities) >= 2, identities))

    start_text = "https://t.me/Moonkkbot?start=30-Register_seeuhmOtV8"
    start_detail = code_rules.extract_code_detail(start_text, safe_only=False)
    checks.append(
        check(
            "telegram start code extracted",
            bool(start_detail.get("code")),
            start_detail,
        )
    )

    invite_text = "https://ep.whooping.top/invite/5fbbddb9"
    invite_detail = code_rules.extract_code_detail(invite_text, safe_only=False)
    checks.append(
        check(
            "invite path code extracted",
            invite_detail.get("code") == "5fbbddb9",
            invite_detail,
        )
    )

    guard = matcher.analyze_message("注册状态：false")
    checks.append(
        check(
            "closed registration guard",
            not guard.get("matched") and guard.get("closed_register_notice"),
            guard,
        )
    )

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
    lottery_policy = rule_policy.default_policy("lottery")
    profile_a = dedup.build_profile(lottery_a, policy=lottery_policy)
    profile_b = dedup.build_profile(lottery_b, policy=lottery_policy)
    checks.append(
        check(
            "lottery identity stable",
            bool(profile_a.get("lottery_identity"))
            and profile_a.get("dedup_id") == profile_b.get("dedup_id"),
            {
                "a": profile_a.get("dedup_id"),
                "b": profile_b.get("dedup_id"),
            },
        )
    )

    generated = rule_generator.generate_rule("lottery", lottery_a)
    checks.append(
        check(
            "plugin rule generation",
            bool(generated.get("pattern")),
            generated,
        )
    )

    ui = plugin_registry.builtin_section("ui", {}) or {}
    checks.append(check("plugin ui schema", bool(ui.get("dedup_simple_fields")), list(ui)))

    child = r'''
import json
import sys
import types

fake = types.ModuleType("redis_store")
fake.get_json = lambda key, default=None: default
fake.set_json = lambda *args, **kwargs: None
fake.smembers = lambda *args, **kwargs: set()
fake.sha = lambda value: str(abs(hash(value)))
fake.format_time = lambda *args, **kwargs: "2026-01-01 00:00:00"
fake.log_line = lambda *args, **kwargs: None
fake.r = types.SimpleNamespace()
sys.modules["redis_store"] = fake
sys.path.insert(0, "/app")

import code_rules
import dedup
import rule_generator

detail = code_rules.extract_code_detail("Wlao-30-Register_jDNZjMTrA")
types_ = [item["id"] for item in rule_generator.available_rule_types()]
profile = dedup.build_profile("普通文本")
print(json.dumps({
    "detail": detail,
    "types": types_,
    "strategy": profile.get("dedup_strategy"),
}, ensure_ascii=False))
'''
    env = dict(os.environ)
    env["SLOWLINK_PURE_MODE"] = "1"
    env["SLOWLINK_ACTIVE_PLUGIN"] = "off"
    proc = subprocess.run(
        [sys.executable, "-c", child],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    pure = {}
    if proc.returncode == 0:
        try:
            pure = json.loads(proc.stdout)
        except Exception:
            pass
    checks.append(
        check(
            "pure core fallback",
            proc.returncode == 0
            and pure.get("detail") == {}
            and pure.get("types") == ["keyword"]
            and pure.get("strategy") == "normalized_text",
            pure or (proc.stderr or proc.stdout),
        )
    )

    result = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
