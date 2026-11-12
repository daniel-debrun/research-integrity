"""Finding passages to quote, and following a citation into a changed document.

`search` ranks passages of the recorded sources' page texts against a question, by BM25 over words, standard library
only. It finds candidates and checks nothing: a passage enters the ledger only through `cite`, which checks it.

`relocate` takes a citation whose source file has changed since it was recorded and runs the same quote check on
the file as it is now: the quote is on the same page, on another page, or gone.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from . import quotecheck as qc
from .quotecheck import Refusal

_TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were which with "
           "we our their these those than then there not no can may also".split())
PASSAGE = 700  # characters: about a paragraph


def _tokens(s: str) -> list:
    return [t for t in _TOKEN.findall(s.lower()) if t not in STOP]


def passages(page: str) -> list:
    """A page cut into paragraph-sized passages, on blank lines, else on sentence ends."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", page) if p.strip()]
    out = []
    for p in parts:
        while len(p) > PASSAGE:
            cut = max(p.rfind(". ", 0, PASSAGE), p.rfind("\n", 0, PASSAGE))
            cut = cut + 1 if cut > PASSAGE // 3 else PASSAGE
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def search(led, query: str, k: int = 8, sources=()) -> dict:
    words = _tokens(query)
    if not words:
        raise Refusal("the query has no words to search for", "ask the question in words")
    docs = []
    for e in led.entries():
        if e["kind"] != "source" or (sources and e["id"] not in sources):
            continue
        forms = led.forms(e["id"])
        for n, page in enumerate(forms[0], 1):
            for text in passages(page):
                docs.append((e, n, text, Counter(_tokens(text))))
    if not docs:
        return {"query": query, "passages": []}
    avg = sum(sum(d[3].values()) for d in docs) / len(docs)
    df = Counter(w for d in docs for w in set(d[3]))
    k1, b = 1.5, 0.75

    def score(tf: Counter) -> float:
        size = sum(tf.values()) or 1
        return sum(math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5)) * tf[w] * (k1 + 1)
                   / (tf[w] + k1 * (1 - b + b * size / avg)) for w in set(words) if tf[w])

    ranked = sorted(((score(d[3]), i) for i, d in enumerate(docs)), reverse=True)
    out = []
    for s, i in ranked[:k]:
        if s <= 0:
            break
        e, n, text, _ = docs[i]
        out.append({"source": e["id"], "origin": e["body"]["origin"], "page": n, "score": round(s, 3),
                    "passage": text, "numbers": sorted(str(x) for x in qc.numbers(text))})
    return {"query": query, "passages": out,
            "note": "candidates only: quote one with cite(source, page, quote, numbers) to record it"}


def relocate(citation: dict, fresh_forms: list) -> dict:
    """Where a citation's quote is in a changed document: `same` page, `moved` (with the pages it is on now), or
    `gone`."""
    b = citation["body"]
    edges = b.get("rules", 1) >= 2
    on = qc.pages_with(fresh_forms, b["quote"], bool(b.get("casefold")), edges)
    if b["page"] in on:
        state = "same"
    elif on:
        state = "moved"
    else:
        state = "gone"
    return {"citation": citation["id"], "source": b["source"], "page": b["page"], "state": state, "pages": on}
