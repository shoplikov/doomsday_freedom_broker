"""Privacy shield: typed masks ([IIN_1], [CARD_1], ...) before any LLM call.

The same value always gets the same token inside one conversation, so criteria 03 (identification)
and 08 (confidential leak) still work on masked text. The raw text stays in the DB for the UI only.
"""
import re

# Order matters: longer / more specific patterns first.
PATTERNS = [
    ("IBAN", re.compile(r"\bKZ\d{2}[A-Z0-9]{13,16}\b", re.I)),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){15,18}\d\b")),
    ("PHONE", re.compile(r"(?:\+7|\b8)[\s(-]*7\d{2}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b")),
    ("IIN", re.compile(r"\b\d{12}\b")),
]


def luhn_ok(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()][::-1]
    total = 0
    for i, d in enumerate(digits):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def mask(text: str, mapping: dict) -> str:
    """Replace personal data with typed tokens. `mapping` is shared across one conversation."""
    for kind, rx in PATTERNS:
        def repl(m, kind=kind):
            value = m.group(0)
            if kind == "CARD" and not luhn_ok(value):
                return value  # long number but not a card (contract id etc.)
            key = (kind, re.sub(r"[\s()-]", "", value).lower())
            if key not in mapping:
                mapping[key] = f"[{kind}_{sum(1 for k in mapping if k[0] == kind) + 1}]"
            return mapping[key]
        text = rx.sub(repl, text)
    return text
