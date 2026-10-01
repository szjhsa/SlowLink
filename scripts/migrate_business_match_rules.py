#!/usr/bin/env python3
"""Move plugin-owned business rules out of the main system's regex set.

Run inside the SlowLink app container. The script never touches plugin
settings or user rules that are not owned by the active plugin.
"""

import argparse
import json
import sys
import time
from pathlib import Path


sys.path.insert(0, "/app")

from config import APP_VERSION  # noqa: E402
from matcher import invalidate_rule_cache  # noqa: E402
from plugin_runtime import call_hook  # noqa: E402
from redis_store import r  # noqa: E402
from rule_policy import policy_key  # noqa: E402


def split_rule_blob(blob: str) -> list[str]:
    return [
        part.strip()
        for part in str(blob or "").split(";;")
        if part.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    business_patterns = {
        str(pattern).strip()
        for pattern in (
            call_hook("get_business_match_patterns", {}, default=[]) or []
        )
        if str(pattern).strip()
    }
    if not business_patterns:
        print("未读取到插件业务规则，已中止，未修改任何数据。")
        return 1

    active_rules = sorted(r.smembers("regex_rules"))
    disabled_rules = sorted(r.smembers("regex_rules_disabled"))
    policies = r.hgetall("rule_policies") or {}

    remove_rules: list[str] = []
    keep_rules: list[str] = []
    for rule in active_rules + disabled_rules:
        parts = split_rule_blob(rule)
        if parts and all(part in business_patterns for part in parts):
            remove_rules.append(rule)
        else:
            keep_rules.append(rule)
    remove_rules = sorted(set(remove_rules))
    keep_rules = sorted(set(keep_rules))

    print(f"应用版本: {APP_VERSION}")
    print(f"插件业务规则: {len(business_patterns)}")
    print(f"待迁移规则: {len(remove_rules)}")
    for rule in remove_rules:
        print(f"  - {rule[:160]}")
    print(f"保留用户规则: {len(keep_rules)}")
    for rule in keep_rules:
        print(f"  + {rule[:160]}")

    if args.dry_run:
        print("dry-run：未修改 Redis。")
        return 0

    backup = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": APP_VERSION,
        "active_rules": active_rules,
        "disabled_rules": disabled_rules,
        "rule_policies": policies,
        "plugin_patterns": sorted(business_patterns),
        "removed_rules": remove_rules,
        "kept_rules": keep_rules,
    }
    backup_path = (
        "/tmp/business-rule-migration-backup-"
        + time.strftime("%Y%m%d_%H%M%S")
        + ".json"
    )
    Path(backup_path).write_text(
        json.dumps(backup, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pipe = r.pipeline()
    for rule in remove_rules:
        pipe.srem("regex_rules", rule)
        pipe.srem("regex_rules_disabled", rule)
        for part in split_rule_blob(rule):
            pipe.hdel("rule_policies", policy_key(part))
        pipe.hdel("rule_policies", policy_key(rule))
    pipe.execute()
    invalidate_rule_cache()

    event = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": "info",
        "message": (
            f"已迁移 {len(remove_rules)} 条业务规则到插件；"
            f"主系统剩余普通规则 {len(keep_rules)} 条"
        ),
        "extra": {"backup": backup_path},
    }
    try:
        r.lpush("events", json.dumps(event, ensure_ascii=False))
        r.ltrim("events", 0, 299)
    except Exception:
        pass

    print(f"迁移完成，备份：{backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
