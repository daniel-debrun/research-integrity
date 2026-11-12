"""What is known about a source beyond its text: its type, how long it stays fresh, and its DOI's updates.

A `meta` entry annotates a source; the latest one for a source holds. Types: primary (data, a standard, the
maker's own record), paper (peer-reviewed or preprint), vendor (a company's page about its product), secondary (a
report or article about others' work), aggregator (a list or database of others' figures). A source recorded with no
type reads as `unknown`, and the chain is never rewritten to add one.

Freshness: vendor and aggregator pages go stale after 90 days by default; the others do not, unless a number of
days is given. A claim resting only on stale sources is listed by `record_draft` and `recall`.

A DOI is looked up at Crossref, asking for the notices that update it (retractions, corrections, expressions of
concern), as the publisher deposited them. The DOI is sent to Crossref; nothing else is.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .quotecheck import Refusal

TYPES = ("primary", "paper", "vendor", "secondary", "aggregator")
FRESH_DAYS = {"vendor": 90, "aggregator": 90}
DOI = re.compile(r"10\.\d{4,9}/\S+")
WARN = ("retraction", "withdrawal", "removal", "expression_of_concern", "correction", "erratum", "corrigendum")
USER_AGENT = "research-ledger (DOI update check)"


def fetch_updates(doi: str) -> list:
    """Crossref notices that update `doi`: [{"type", "doi", "date"}]."""
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode({"filter": f"updates:{doi}", "rows": 20})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            items = json.loads(r.read())["message"]["items"]
    except (OSError, ValueError, KeyError) as e:
        raise Refusal("Crossref could not be asked", str(e)[:200], "try again, or record the source without a DOI")
    out = []
    for it in items:
        for u in it.get("update-to", []):
            if u.get("DOI", "").lower() == doi.lower():
                parts = (u.get("updated") or {}).get("date-parts", [[None]])[0]
                out.append({"type": u.get("type", ""), "doi": it.get("DOI", ""),
                            "date": "-".join(str(p) for p in parts if p)})
    return out


def annotate(led, source: str, source_type: str = "", stale_after_days: int | None = None, doi: str = "",
             fetch=fetch_updates) -> dict:
    src = led.get(source, "source")
    if source_type and source_type not in TYPES:
        raise Refusal(f"`{source_type}` is not a source type", f"the types are {', '.join(TYPES)}")
    if stale_after_days is not None and (not isinstance(stale_after_days, int) or stale_after_days < 1):
        raise Refusal("stale_after_days is a whole number of days, 1 or more")
    if doi:
        doi = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
        if not DOI.fullmatch(doi):
            raise Refusal(f"`{doi}` is not a DOI", "a DOI starts 10. and has a slash, such as 10.1038/nature12373")
    if not (source_type or stale_after_days or doi):
        raise Refusal("nothing to record", "give a source_type, stale_after_days or a doi")
    old = current(led.entries(), src["id"])
    body = {"source": src["id"], "source_type": source_type or old.get("source_type", ""),
            "stale_after_days": stale_after_days if stale_after_days is not None else old.get("stale_after_days"),
            "doi": doi or old.get("doi", "")}
    if doi:
        body["updates"] = fetch(doi)
        body["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    elif old.get("updates") is not None:
        body["updates"], body["checked"] = old["updates"], old.get("checked")
    return led._append("meta", {k: v for k, v in body.items() if v not in (None, "")})


def current(entries, source_id: str) -> dict:
    m = [e["body"] for e in entries if e["kind"] == "meta" and e["body"]["source"] == source_id]
    return m[-1] if m else {}


def status(entries, source: dict, now=None) -> dict:
    """A source's type, whether it is stale, and its warning notices."""
    m = current(entries, source["id"])
    t = m.get("source_type", "unknown")
    days = m.get("stale_after_days") or FRESH_DAYS.get(t)
    now = now or datetime.now(timezone.utc)
    stale = bool(days) and datetime.fromisoformat(source["at"]) < now - timedelta(days=days)
    notices = [u for u in m.get("updates", []) if any(w in u["type"].lower() for w in WARN)]
    return {"type": t, "stale": stale, "notices": notices}


def claim_warnings(entries, now=None) -> list:
    """Standing claims resting only on stale or untyped sources, or on any source with a retraction or correction."""
    by_id = {e["id"]: e for e in entries}
    replaced = {e["body"].get("supersedes") for e in entries if e["kind"] == "claim"} - {"", None}
    out = []
    for e in entries:
        if e["kind"] != "claim" or e["id"] in replaced:
            continue
        srcs = [by_id[by_id[c]["body"]["source"]] for c in e["body"]["citations"]
                if c in by_id and by_id[c]["body"].get("source") in by_id
                and by_id[by_id[c]["body"]["source"]]["kind"] == "source"]
        if not srcs:
            continue
        st = [status(entries, s, now) for s in srcs]
        why = []
        if all(s["stale"] for s in st):
            why.append("rests only on stale sources")
        if all(s["type"] == "unknown" for s in st):
            why.append("rests only on sources of unknown type")
        for s, x in zip(srcs, st):
            for n in x["notices"]:
                why.append(f"{s['id']} has a {n['type']} notice ({n['doi']})")
        if why:
            out.append({"claim": e["id"], "text": e["body"]["text"], "why": why})
    return out


def verify_entry(led, e: dict, seen: dict) -> None:
    b = e["body"]
    if seen.get(b["source"], {}).get("kind") != "source":
        raise Refusal(f"annotates {b['source']}, which is not an earlier source")
    if b.get("source_type") and b["source_type"] not in TYPES:
        raise Refusal(f"gives the type {b['source_type']}, which is not a source type")
