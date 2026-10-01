import re
from typing import Any

import regex as _regex

from plugin_runtime import call_hook
from rule_policy import default_policy
from rule_types import available_rule_types as available_rule_types, get_rule_type_config

__all__ = ["available_rule_types", "generate_rule"]


def _line_pattern(line: str) -> str:
    parts = [part for part in re.split(r"\s+", line.strip()) if part]
    if not parts:
        raise ValueError("没有可生成的文本")
    return r"(?m)^\s*" + r"\s+".join(re.escape(part) for part in parts) + r"\s*$"


def _first_meaningful_line(sample: str) -> str:
    for raw in str(sample or "").splitlines():
        line = raw.strip()
        if line and not re.fullmatch(r"[━─—=\-]+", line):
            return line
    return ""


def generate_rule(rule_type: str, sample: str) -> dict[str, Any]:
    config = get_rule_type_config(rule_type)
    normalized_type = str(config["id"])
    strategy = str(config["strategy"])
    raw = str(sample or "")
    if not raw.strip():
        raise ValueError("原消息不能为空")
    if len(raw) > 8192:
        raw = raw[:8192]

    result = call_hook(
        "generate_rule",
        {
            "rule_type": normalized_type,
            "strategy": strategy,
            "sample": raw,
            "config": config,
        },
        default=None,
    )
    if isinstance(result, dict) and str(result.get("pattern") or "").strip():
        pattern = str(result.get("pattern") or "").strip()
        reason = str(result.get("reason") or "由插件生成")
    elif strategy == "line":
        line = _first_meaningful_line(raw)
        if not line:
            raise ValueError("没有可生成的文本")
        pattern = _line_pattern(line)
        reason = "按首条有效文本生成精确匹配规则"
    else:
        raise ValueError("当前插件未提供该规则类型的生成器")

    _regex.compile(pattern, re.I | re.M)
    policy = default_policy(normalized_type)
    return {
        "pattern": pattern,
        "rule_type": normalized_type,
        "policy": policy,
        "reason": reason,
        "sample": raw[:1200],
    }
