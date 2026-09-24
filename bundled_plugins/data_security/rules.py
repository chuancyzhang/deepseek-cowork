"""Bounded, conservative local candidate recognition. No external services."""
import datetime
import ipaddress
import re

VERSION = "1"
PATTERNS = {
    "id_card": re.compile(r"(?<![0-9A-Za-z])([1-9]\d{16}[\dXx])(?![0-9A-Za-z])"),
    "bank_card": re.compile(r"(?<!\d)([1-9]\d{15,18})(?!\d)"),
    "phone": re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)"),
    "email": re.compile(r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,5})(?![\w.-])"),
    "landline": re.compile(r"(?:电话|座机|tel(?:ephone)?)[：:\s]{0,8}(0\d{2,3}[- ]?\d{7,8})(?!\d)", re.I),
    "passport": re.compile(r"(?:护照(?:号)?|passport)[：:=\s]{0,8}([A-Z][A-Z0-9]{5,8})(?![A-Za-z0-9])", re.I),
    "credit_code": re.compile(r"(?<![A-Z0-9])([159Y][1239]\d{6}[0-9ABCDEFGHJKLMNPQRTUWXY]{10})(?![A-Z0-9])"),
    "ip": re.compile(r"(?<![\w.:])((?:\d{1,3}\.){3}\d{1,3}|[A-Fa-f0-9:]{2,45}:[A-Fa-f0-9:]{0,8})(?![\w.:])"),
}
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret)\s{0,8}[\"']?[:=]\s{0,8}[\"']?([A-Za-z0-9_./+\-=]{8,256}))|"
    r"\bBearer[ \t]{1,8}[A-Za-z0-9._~+/=-]{16,512}"
)
EXAMPLES = {"password", "changeme", "your_api_key", "your_token", "xxxxxxxx", "example", "placeholder"}


def valid(category, value):
    if category == "id_card":
        try:
            datetime.datetime.strptime(value[6:14], "%Y%m%d")
        except ValueError:
            return False
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        return "10X98765432"[sum(int(n) * w for n, w in zip(value[:17], weights)) % 11] == value[-1].upper()
    if category == "bank_card":
        if len(set(value)) < 3:
            return False
        digits = [int(n) for n in value[::-1]]
        return sum(n if i % 2 == 0 else (2*n if n < 5 else 2*n-9) for i, n in enumerate(digits)) % 10 == 0
    if category == "credit_code":
        alphabet = "0123456789ABCDEFGHJKLMNPQRTUWXY"
        weights = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
        return alphabet[(31 - sum(alphabet.index(c)*w for c, w in zip(value[:17], weights)) % 31) % 31] == value[-1]
    if category == "ip":
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return False
    return True


def matches(text, categories):
    spans = []
    protected = [match.span() for match in re.finditer(r"\[\[CW:[^\]\n]{1,100}\]\]", text)]
    for kind, pattern in PATTERNS.items():
        if not categories.get(kind):
            continue
        for match in pattern.finditer(text):
            if not any(a <= match.start(1) < b for a, b in protected) and valid(kind, match[1]):
                spans.append((*match.span(1), kind, match[1]))
                if len(spans) > 10000:
                    raise ValueError("match_limit")
    # Prefer the longest match at a position; never tokenize a substring twice.
    end = -1
    for span in sorted(spans, key=lambda item: (item[0], -(item[1]-item[0]))):
        if span[0] >= end:
            yield span
            end = span[1]


def credential_count(text):
    return sum(1 for match in SECRET.finditer(text)
               if not match[1] or match[1].lower() not in EXAMPLES)
