"""Signals layer: facts computed by code and handed to the LLM, so it never guesses timings,
order of events or languages."""

KZ_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
KZ_WORDS = {"рахмет", "керек", "болады", "иә", "жоқ"}
GAP_SECONDS = 120  # a wait this long before an operator reply is reported as a gap
FIRST_REACTION_LIMIT = 180  # checklist: first reaction threshold is 3 minutes
# System events, lowercase substrings, RU / KZ / EN.
VERIFIED = ("identity verified", "личность клиента подтверждена", "личность подтверждена", "жеке басы расталды")
CLIENT_GONE = ("connection to customer lost", "customer left", "соединение с клиентом", "клиент покинул",
               "клиент завершил", "байланыс үзілді", "чаттан шықты")
WAIT_WORDS = ("минут", "момент", "ожида", "подожд", "провер", "уточн", "күт", "тексер", "minute", "moment", "wait",
              "hold", "check")

# Hints only: the LLM still decides. Lowercase substrings in OPERATOR messages, grouped by the gross error they hint at.
RISK_TERMS = {
    "side_deal": ["на мою карту", "мне лично", "переведите мне", "мой личный", "личный номер", "в обход", "whatsapp",
                  "маған жеке", "менің картама", "my personal card", "my personal account", "transfer to me",
                  "privately", "message me at"],
    "insult": ["дурак", "идиот", "тупой", "ақымақ", "idiot", "stupid"],
    # another person's data: family members, "Mr./Mrs. X", other clients
    "another_person": ["другой клиент", "другого клиента", "у других клиентов", "супруг", "вашей жены", "вашего мужа",
                       "мырза", "ханым", "жұбай", "әйеліңіз", "күйеуіңіз", "басқа клиент",
                       "another client", "other client", "another customer", "your wife", "your husband"],
    "internal_info": ["по секрету", "внутренн", "комплаенс", "между нами", "құпия", "ішкі ереже",
                      "internal rule", "internal limit", "between us", "secret"],
}


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
        # The clock starts when the chat reached the operator (the transfer event), not at the bot greeting.
        before = [m for m in msgs[: op["idx"]] if m["speaker"] == "system"] or clients[:1]
        start = before[-1] if before else None
        delay = None
        if start and op["t_sec"] is not None and start["t_sec"] is not None:
            delay = round(op["t_sec"] - start["t_sec"])
        first_reply = {"mid": op["mid"], "ts": op["ts_raw"], "counted_from": start["mid"] if start else None,
                       "seconds_after_reaching_operator": delay,
                       "over_3_min_first_reaction_limit": None if delay is None else delay > FIRST_REACTION_LIMIT}

    # Waits inside the operator's part of the chat: a client message, then 2+ minutes before the operator answers.
    waits = []
    start_idx = ops[0]["idx"] if ops else len(msgs)
    for a, b in zip(msgs, msgs[1:]):
        if a["idx"] < start_idx or b["speaker"] != "operator" or a["speaker"] != "client":
            continue
        if a["t_sec"] is None or b["t_sec"] is None or b["t_sec"] - a["t_sec"] < GAP_SECONDS:
            continue
        prev_op = [m for m in ops if m["idx"] < a["idx"]]
        announced = bool(prev_op) and any(w in prev_op[-1]["text_masked"].lower() for w in WAIT_WORDS)
        waits.append({"client_message": a["mid"], "operator_reply": b["mid"], "seconds": round(b["t_sec"] - a["t_sec"]),
                      "operator_warned_before": announced})

    risk_hits = [
        {"mid": m["mid"], "kind": kind, "term": term}
        for m in ops for kind, terms in RISK_TERMS.items() for term in terms if term in m["text_masked"].lower()
    ]

    return {
        "message_count": len(msgs),
        "first_operator_reply": first_reply,
        "operator_waits_over_2_min": waits,
        "identity_verified_events": [m["mid"] for m in msgs if m["speaker"] == "system"
                                     and any(k in m["text_masked"].lower() for k in VERIFIED)],
        "client_left_events": [m["mid"] for m in msgs if m["speaker"] == "system"
                               and any(k in m["text_masked"].lower() for k in CLIENT_GONE)],
        "language_profile": {"client": _profile(clients), "operator": _profile(ops)},
        "system_events": [{"mid": m["mid"], "text": m["text_masked"]} for m in msgs if m["speaker"] == "system"],
        "risk_hits": risk_hits,
        "last_operator_message": ops[-1]["mid"] if ops else None,
    }
