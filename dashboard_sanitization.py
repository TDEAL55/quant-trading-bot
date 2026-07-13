import re


_SANITIZE_PATTERNS = [
    r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)",
    r"(?i)(api[_-]?secret\s*[=:]\s*)([^\s,;]+)",
    r"(?i)(authorization\s*[=:]\s*)(?:bearer\s+)?([^\s,;]+)",
    r"(?i)(token\s*[=:]\s*)([^\s,;]+)",
    r"(?i)(account(?:[_-]?(?:id|number))?\s*[=:]\s*)([^\s,;]+)",
]


def sanitize_text(value, fallback=""):
    text = str(value or "").strip()
    if not text:
        return fallback
    safe = text
    for pattern in _SANITIZE_PATTERNS:
        safe = re.sub(pattern, r"\1[REDACTED]", safe)
    safe = re.sub(r"\b\d{8,}\b", "[REDACTED]", safe)
    return safe


def sanitize_identifier(value, visible_prefix=2, visible_suffix=2):
    text = sanitize_text(value, "")
    if not text:
        return ""
    if len(text) <= visible_prefix + visible_suffix + 3:
        return text
    return f"{text[:visible_prefix]}…{text[-visible_suffix:]}"
