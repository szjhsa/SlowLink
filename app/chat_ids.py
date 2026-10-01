"""Dependency-free Telegram chat ID normalization shared by core modules."""


def ordered_dialog_id_variants(value) -> list[str]:
    """Return ID variants ordered from the most explicit form to the broadest.

    Telegram uses -100<id> for channels/supergroups and -<id> for basic
    groups. Inventing the other form makes two different chats collide in the
    entity cache, so only the valid conversion is added here.
    """
    keys: list[str] = []
    seen: set[str] = set()

    def add(item) -> None:
        key = str(item or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    if value is None:
        return keys
    s = str(value).strip().lower()
    if not s:
        return keys
    add(s)
    if s.startswith("-100") and s[4:].isdigit():
        add(s[4:])
    elif s.startswith("-") and s[1:].isdigit():
        add(s[1:])
    elif s.isdigit():
        add("-100" + s)
    return keys


def dialog_id_variants(value) -> set[str]:
    """Return normalized variants for a Telegram chat/channel id."""
    return set(ordered_dialog_id_variants(value))
