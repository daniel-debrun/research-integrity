"""An append-only, hash-chained ledger of sources, citations and claims, checked at write time.

The ledger is a folder: `chain.jsonl` holds one entry per line, each carrying the sha256 of the one before, and
`blobs/` holds the documents' page texts by their sha256. An entry is checked before it is written and is never
changed after; `verify` walks the chain from the start, recomputes every hash and runs every citation's check again
against the stored pages and, where a source's file is still there, against the document itself.

Kinds:
- source: a document as read (its bytes' sha256, its page texts as a blob).
- instrument, run, reproduction: a program recorded by its exact code and environment, one run of it, and a rerun
  compared with it (see instruments.py).
- citation: a verbatim quote on one page of a source, or in a run's output, and the numbers it is cited for, each
  written in the quote.
- claim: a statement resting on citations; every number the statement writes is written in a quote it cites. A
  claim may supersede an earlier one.
- draft, critique, answer: a document's versions, a reviewer's findings on them (or on a claim, run or citation), and
  the answer to each finding (see critique.py).
- node, launch: a compute node's fingerprint, and a run started on one, recorded before it starts (research-compute).
- plan, look: what a study tests, sealed before the runs it governs, and one look at its holdout (see plans.py). A
  run or launch tagged to a plan's hypothesis is one of its trials; a claim resting on one records the count.
- model, verdict: a hypothesis's machine-checked formal model, and its computed verdict (research-formal).

`work/` (snapshot copies and run folders) and `.lock` are not part of the record; the ledger's .gitignore says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import critique as cq
from . import formal as fm
from . import instruments as ins
from . import plans as pl
from . import quotecheck as qc
from .quotecheck import Refusal

try:
    import fcntl

    def _lock(f):
        fcntl.flock(f, fcntl.LOCK_EX)
except ImportError:  # Windows
    import msvcrt

    def _lock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

GENESIS = "0" * 64
#: The checks an entry was recorded under. 1: v0.2.0. 2: a quote may not cut a number on its page, a citation may
#: casefold, and a claim's numbers (labels aside) must be among the numbers its citations are cited for. `verify`
#: rechecks every entry under the rules it was recorded with.
RULES = 2
USER_AGENT = "research-ledger/0.1 (citation check)"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def entry_hash(seq: int, kind: str, at: str, body: dict, prev: str) -> str:
    return hashlib.sha256(canonical({"seq": seq, "kind": kind, "at": at, "body": body, "prev": prev})).hexdigest()


def _how(body: dict) -> tuple:
    """How a citation's quote is rechecked: (casefold, edges), by the rules it was recorded under."""
    return bool(body.get("casefold")), body.get("rules", 1) >= 2


def claim_backing(citations: list, rules: int) -> set:
    """The numbers a claim's citations back: under rules 1, every number their quotes write; under rules 2, the
    numbers each was cited for."""
    if rules >= 2:
        return {qc.as_number(n) for c in citations for n in c["body"]["numbers"]}
    return set().union(*(qc.numbers(c["body"]["quote"]) for c in citations))


class Ledger:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.chain = self.root / "chain.jsonl"
        self.blobs = self.root / "blobs"

    # --- reading --------------------------------------------------------------------------------------------------
    def entries(self) -> list:
        if not self.chain.exists():
            return []
        return [json.loads(line) for line in self.chain.read_text(encoding="utf-8").splitlines() if line.strip()]

    def get(self, id: str, kind: str = "") -> dict:
        e = next((e for e in self.entries() if e["id"] == id), None)
        if e is None or (kind and e["kind"] != kind):
            what = f"a {kind}" if kind else "an entry"
            raise Refusal(f"`{id}` is not {what} of the ledger", "ids are as the tools that record entries return them",
                          "list the ledger's entries to find it")
        return e

    def blob(self, sha: str) -> bytes:
        return (self.blobs / sha).read_bytes()

    def forms(self, source_id: str) -> list:
        """A source's page texts, or a draft's text as its one page."""
        e = self.get(source_id)
        if e["kind"] == "draft":
            return [[self.blob(e["body"]["text"]).decode("utf-8")]]
        if e["kind"] != "source":
            raise Refusal(f"`{source_id}` is a {e['kind']}, not a source or draft", "a quote comes from a page of a "
                          "source or from a draft", "cite_run quotes a run's output")
        return json.loads(self.blob(e["body"]["pages"]))

    # --- writing --------------------------------------------------------------------------------------------------
    def _put_blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        self.blobs.mkdir(parents=True, exist_ok=True)
        path = self.blobs / sha
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return sha

    def _append(self, kind: str, body: dict, check=lambda: None) -> dict:
        """Check and append one entry under an exclusive lock, so two writers never fork the chain."""
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".gitignore").exists():
            (self.root / ".gitignore").write_text(".lock\nwork/\n")
        with open(self.root / ".lock", "w") as lock:
            _lock(lock)
            check()
            entries = self.entries()
            prev = entries[-1]["hash"] if entries else GENESIS
            seq = len(entries)
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            h = entry_hash(seq, kind, at, body, prev)
            e = {"seq": seq, "id": f"{kind}:{h[:12]}", "kind": kind, "at": at, "body": body, "prev": prev, "hash": h}
            with open(self.chain, "a", encoding="utf-8") as f:
                f.write(canonical(e).decode("utf-8") + "\n")
                f.flush()
                os.fsync(f.fileno())
            return e

    def add_source(self, origin: str) -> dict:
        """Read a document (a local file, or an http(s) URL) and record it. The same bytes twice return the
        entry already there."""
        if origin.startswith(("http://", "https://")):
            req = urllib.request.Request(origin, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
            except OSError as e:
                raise Refusal(f"could not fetch {origin}", str(e)[:200], "check the URL, or download it and give the file")
        else:
            path = Path(origin).expanduser()
            draft = next((e for e in reversed(self.entries()) if e["kind"] == "draft"
                          and Path(e["body"]["path"]).expanduser().resolve() == path.resolve()), None)
            if draft:
                raise Refusal(f"{origin} is recorded as the draft {draft['id']}", "a draft's text is in the ledger "
                              "already, at the exact version a critique targets; as a source it would go stale when "
                              "the draft is revised", f"quote it with cite(source=\"{draft['id']}\", page=1)")
            if not path.is_file():
                raise Refusal(f"no file at {origin}", "a source is a local file or an http(s) URL",
                              "give the document's path or URL")
            data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        known = next((e for e in self.entries() if e["kind"] == "source" and e["body"]["sha256"] == sha), None)
        if known:
            return {**known, "already": True}
        forms = qc.forms_of(data, origin)
        if not any(p.strip() for f in forms for p in f):
            raise Refusal("the document has no text", "a scanned PDF has none to quote", "give a text layer (OCR it first)")
        pages = self._put_blob(canonical(forms))
        body = {"origin": origin, "sha256": sha, "pages": pages, "n_pages": max(map(len, forms)),
                "layout": len(forms) > 1}
        return self._append("source", body)

    def cite(self, source: str, page: int, quote: str, cited_numbers=(), casefold: bool = False) -> dict:
        """Record a quote on one page of a source, refused unless it is on that page (not cutting a number the page
        writes) and writes every number. `casefold` matches letters regardless of case."""
        src = self.get(source)
        out = qc.check(self.forms(source), page, quote, cited_numbers, casefold)
        body = {"source": src["id"], "page": page, "quote": quote, "numbers": out["numbers"], "rules": RULES,
                **({"casefold": True} if casefold else {})}
        return self._append("citation", body)

    def cite_run(self, run: str, quote: str, cited_numbers=(), file: str = "stdout", casefold: bool = False) -> dict:
        """Record a quote from a run's output (its stdout, its stderr or a text output), refused unless it is
        there and writes every number."""
        r = self.get(run, "run")
        out = qc.check([[ins.output_text(self, r, file)]], 1, quote, cited_numbers, casefold)
        return self._append("citation", {"run": r["id"], "file": file, "quote": quote, "numbers": out["numbers"],
                                         "rules": RULES, **({"casefold": True} if casefold else {})})

    def claim(self, text: str, citations: list, supersedes: str = "") -> dict:
        """Record a statement resting on citations, refused unless every number it writes is written in a quote
        it cites. `supersedes` replaces an earlier claim, refused while a blocking finding on it is unanswered."""
        if not text.strip():
            raise Refusal("a claim needs its statement")
        if not citations:
            raise Refusal("a claim rests on at least one citation", "a statement with no quote behind it is not "
                          "recorded", "cite the passage it rests on first, then claim with the citation's id")
        cited = [self.get(c, "citation") for c in citations]
        loose = sorted(qc.claim_numbers(text) - claim_backing(cited, RULES))
        if loose:
            raise Refusal(f"{loose[0]} is not a number any citation of the claim is cited for",
                          "every number a claim writes (a label's, such as Table 4, aside) is one its citations were "
                          "made for; a number that only happens to be in a quote is not checked against the page",
                          "cite the passage with that number in `numbers`, write it as the quote does, or drop it")
        body = {"text": text, "citations": [c["id"] for c in cited], "rules": RULES}
        trials = pl.claim_trials(self.entries(), cited)
        if trials:
            body["trials"] = trials
        if supersedes:
            old = self.get(supersedes, "claim")
            cq.gate(self.entries(), old["id"])
            body["supersedes"] = old["id"]
        e = self._append("claim", body)
        words = cq.wording(text)
        return {**e, "wording": words} if words else e

    # --- checking -------------------------------------------------------------------------------------------------
    def document(self, origin: str) -> bytes | None:
        """A source's bytes read again from the file it was recorded from (a relative path from the working
        directory, else from the project the ledger sits in); None for a URL or a file that is no longer there."""
        if origin.startswith(("http://", "https://")):
            return None
        path = Path(origin).expanduser()
        for p in (path,) if path.is_absolute() else (path, self.root.parent / path):
            if p.is_file():
                return p.read_bytes()
        return None

    def verify(self) -> dict:
        """Walk the chain: each hash recomputed, each link to an earlier entry, each blob intact, each citation's
        check run again on the stored pages, each claim's numbers in its quotes. A source whose file is still there
        is read again: its bytes must hash as recorded, and each citation's check runs on its pages read afresh, so
        page texts written by hand show up too. `unread` lists the sources that could not be (URLs, files gone)."""
        problems, seen, prev, fresh, unread = [], {}, GENESIS, {}, []
        for i, e in enumerate(self.entries()):
            where = f"entry {i} ({e.get('id', '?')})"
            if not {"seq", "id", "kind", "at", "body", "prev", "hash"} <= e.keys():
                problems.append(f"{where}: not a ledger entry (fields missing)")
                prev = e.get("hash", "")
                continue
            if e.get("seq") != i or e.get("prev") != prev:
                problems.append(f"{where}: the chain is broken here (an entry removed, reordered or inserted)")
            if entry_hash(e["seq"], e["kind"], e["at"], e["body"], e["prev"]) != e.get("hash") \
                    or e["id"] != f"{e['kind']}:{e['hash'][:12]}":
                problems.append(f"{where}: its content does not match its hash (edited after it was written)")
            prev = e.get("hash", "")
            b = e["body"]
            try:
                if e["kind"] == "source":
                    if hashlib.sha256(self.blob(b["pages"])).hexdigest() != b["pages"]:
                        problems.append(f"{where}: its page texts were changed")
                    data = self.document(b["origin"])
                    if data is None:
                        unread.append(e["id"])
                    elif hashlib.sha256(data).hexdigest() != b["sha256"]:
                        raise Refusal("its file has changed since it was recorded", b["origin"])
                    else:
                        fresh[e["id"]] = qc.forms_of(data, b["origin"])
                elif e["kind"] in ("instrument", "run", "node", "launch", "reproduction"):
                    ins.verify_entry(self, e, seen)
                    pl.verify_entry(self, e, seen)
                elif e["kind"] in ("plan", "look"):
                    pl.verify_entry(self, e, seen)
                elif e["kind"] in ("model", "verdict"):
                    fm.verify_entry(self, e, seen)
                elif e["kind"] in ("draft", "critique", "answer"):
                    cq.verify_entry(self, e, seen)
                elif e["kind"] == "citation" and "run" in b:
                    if seen.get(b["run"], {}).get("kind") != "run":
                        raise Refusal(f"cites {b['run']}, which is not an earlier run")
                    qc.check([[ins.output_text(self, seen[b["run"]], b["file"])]], 1, b["quote"], b["numbers"],
                             *_how(b))
                elif e["kind"] == "citation":
                    if seen.get(b["source"], {}).get("kind") not in ("source", "draft"):
                        raise Refusal(f"cites {b['source']}, which is not an earlier source or draft")
                    qc.check(self.forms(b["source"]), b["page"], b["quote"], b["numbers"], *_how(b))
                    if b["source"] in fresh:
                        try:
                            qc.check(fresh[b["source"]], b["page"], b["quote"], b["numbers"], *_how(b))
                        except Refusal as r:
                            raise Refusal(f"in the document itself, {r.error}", r.meaning)
                elif e["kind"] == "claim":
                    if any(seen.get(c, {}).get("kind") != "citation" for c in b["citations"]):
                        raise Refusal("rests on an id that is not an earlier citation")
                    rules = b.get("rules", 1)
                    told = qc.claim_numbers(b["text"]) if rules >= 2 else qc.numbers(b["text"])
                    if told - claim_backing([seen[c] for c in b["citations"]], rules):
                        raise Refusal("writes a number none of its citations back")
                    if b.get("supersedes") and seen.get(b["supersedes"], {}).get("kind") != "claim":
                        raise Refusal(f"supersedes {b['supersedes']}, which is not an earlier claim")
                    if b.get("trials", []) != pl.claim_trials(list(seen.values()), [seen[c] for c in b["citations"]]):
                        raise Refusal("its trial count is not what the record shows")
            except Refusal as r:
                problems.append(f"{where}: {r.error}" + (f" ({r.meaning})" if r.meaning else ""))
            except FileNotFoundError:
                problems.append(f"{where}: a blob it needs is missing")
            seen[e["id"]] = e
        kinds = ("source", "instrument", "run", "reproduction", "citation", "claim", "draft", "critique", "answer",
                 "node", "launch", "plan", "look", "model", "verdict")
        counts = {k: sum(1 for v in seen.values() if v["kind"] == k) for k in kinds}
        review = cq.summary(list(seen.values()))
        problems += review["problems"]
        ran = {v["body"].get("launch") for v in seen.values() if v["kind"] == "run"}
        open_launches = [v["id"] for v in seen.values() if v["kind"] == "launch" and v["id"] not in ran]
        reps = [v for v in seen.values() if v["kind"] == "reproduction"]
        return {"ok": not problems, "entries": len(seen), **counts, "head": prev if seen else GENESIS,
                "problems": problems, "unread": unread, "open_findings": review["open_findings"],
                "reproductions": {"reproduced": sum(r["body"]["verdict"] == "reproduced" for r in reps),
                                  "differs": [r["id"] for r in reps if r["body"]["verdict"] == "differs"]},
                "open_launches": open_launches, "personas": review["personas"],
                "needs_revision": fm.needs_revision(list(seen.values())),
                "trials": [{"plan": n, "hypothesis": h["id"], **{k: h[k] for k in ("trials", "reported")},
                            "dropped": len(h["dropped"])}
                           for n in sorted({v["body"]["name"] for v in seen.values() if v["kind"] == "plan"})
                           for h in pl.status(list(seen.values()), n)["hypotheses"]]}
