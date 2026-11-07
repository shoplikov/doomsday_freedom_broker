"""Signals layer: facts computed by code and handed to the LLM, so it never guesses timings,
order of events or languages."""

KZ_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
KZ_WORDS = {"рахмет", "керек", "болады", "иә", "жоқ"}
GAP_SECONDS = 120  # a wait this long before an operator reply is reported as a gap

# Hints only: the LLM still decides. Lowercase substrings.
RISK_TERMS = [
    # side deal / off-platform money
    "на мою карту", "мне лично", "переведите мне", "мой личный", "личный номер", "в обход",
    "маған жеке", "менің картама",
    "my personal card", "my personal account", "transfer to me", "privately", "message me at",
    # insults
    "дурак", "идиот", "тупой", "ақымақ", "idiot", "stupid",
]


def detect_lang(text: str) -> str | None:
    """Per-message language: KZ / RU / EN / None. ponytail: letter heuristic; KZ text without
    KZ-specific letters reads as RU. Upgrade path: LLM or fastText fallback for short messages."""
    low = text.lower()
    if any(ch in KZ_LETTERS for ch in text) or any(w in low.split() for w in KZ_WORDS):
        return "KZ"
    cyr = sum("а" <= ch <= "я" or ch == "ё" for ch in low)
    lat = sum("a" <= ch <= "z" for ch in low)
    if not cyr and not lat:
        return None
    return "RU" if cyr >= lat else "EN"


def _profile(msgs: list[dict]) -> dict:
    langs = [m["lang"] for m in msgs if m.get("lang")]
    return {lang: round(100 * langs.count(lang) / len(langs)) for lang in sorted(set(langs))} if langs else {}


def compute(msgs: list[dict]) -> dict:
    ops = [m for m in msgs if m["speaker"] == "operator"]
    clients = [m for m in msgs if m["speaker"] == "client"]

    first_reply = None
    if ops:
        op = ops[0]
        first_client = clients[0] if clients else None
        delay = None
        if first_client and op["t_sec"] is not None and first_client["t_sec"] is not None:
            delay = round(op["t_sec"] - first_client["t_sec"])
        first_reply = {"mid": op["mid"], "ts": op["ts_raw"], "seconds_after_first_client_message": delay}

    gaps = []
    for a, b in zip(msgs, msgs[1:]):
        if b["speaker"] == "operator" and a["t_sec"] is not None and b["t_sec"] is not None:
            wait = b["t_sec"] - a["t_sec"]
            if wait >= GAP_SECONDS:
                gaps.append({"before": b["mid"], "after": a["mid"], "seconds": round(wait)})

    risk_hits = [
        {"mid": m["mid"], "term": term}
        for m in ops for term in RISK_TERMS if term in m["text_masked"].lower()
    ]

    return {
        "message_count": len(msgs),
        "first_operator_reply": first_reply,
        "operator_reply_gaps": gaps,
        "language_profile": {"client": _profile(clients), "operator": _profile(ops)},
        "system_events": [{"mid": m["mid"], "text": m["text_masked"]} for m in msgs if m["speaker"] == "system"],
        "risk_hits": risk_hits,
        "last_operator_message": ops[-1]["mid"] if ops else None,
    }
