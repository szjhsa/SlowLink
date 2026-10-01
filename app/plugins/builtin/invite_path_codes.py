import re


INVITE_PATH_CODE_RE = re.compile(
    r"https?://[^\s/?#]+/invite/([A-Za-z0-9]{6}(?:[A-Za-z0-9]{2})?)"
    r"(?=$|[?#\s]|[，。！？？；：、）】]|[,.;:)\]}>`~*])",
    re.I,
)


def extract_invite_path_codes(text: str) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for match in INVITE_PATH_CODE_RE.finditer(text or ""):
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def extract_first_invite_path_code(text: str) -> str:
    codes = extract_invite_path_codes(text)
    return codes[0] if codes else ""
